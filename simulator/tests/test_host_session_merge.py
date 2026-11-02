"""Full merger against genuine launcher manifest, signed hosts and signed clocks."""
import copy
import json
import pathlib
import subprocess
import sys
import unittest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

SIM=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SIM))
from distributed_merge import merge
from host_signed_evidence import make_envelope
from host_session import create_session,digest


class LiveCollectorContractTests(unittest.TestCase):
    def setUp(self):
        from test_distributed_integrity import IntegrityTests
        self.fixture=IntegrityTests('test_valid_manifest_and_scripts')
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.root=self.fixture.f.root
        self.paths=self.fixture.paths
        self.manifest=self.fixture.manifest
        self.manifest_path=self.root/'manifest.json'
        self.manifest_path.write_text(json.dumps(self.manifest))
        self.pins={};self.keys=[]
        for i in range(2):
            key=Ed25519PrivateKey.generate()
            self.pins[f'host-{i}']=key.public_key().public_bytes(
                serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode()
            path=self.root/f'signing-{i}.pem'
            path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                           serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
            path.chmod(0o600)
            self.keys.append(path)
        self.pins_path=self.root/'pins.json';self.pins_path.write_text(json.dumps(self.pins))
        self.session=create_session(self.manifest,self.pins,['host-0','host-1'])
        self.session_path=self.root/'session.json';self.session_path.write_text(json.dumps(self.session))
        self.reports=[]
        self.sign()

    def sign(self,clock_reports=None):
        self.reports=[]
        for i,folder in enumerate(self.paths):
            signed=make_envelope(folder,'testm7',f'host-{i}',i,self.keys[i],
                    session=self.session,manifest=self.manifest,pins=self.pins,
                    clock_report=clock_reports[i] if clock_reports else None)
            path=self.root/f'signed-{i}.json';path.write_text(json.dumps(signed))
            self.reports.append(path)

    def check(self,**kwargs):
        options={'launch_manifest':self.manifest_path,'host_session':self.session_path,
                 'host_pins':self.pins_path,'signed_host_reports':self.reports}
        options.update(kwargs)
        return merge(self.paths,**options)

    def test_valid_complete_merger_with_bound_signed_session(self):
        result=self.check()
        self.assertEqual(result['outcome'],'PASS',result['harness_errors'])
        self.assertEqual(result['session_sha256'],digest(self.session))
        self.assertEqual(result['signed_host_evidence']['status'],
                         'ALL_PINNED_SIGNING_KEYS_VERIFIED_NOT_HOST_ATTESTATION')

    def test_reversed_arrival_order_is_not_reassigned(self):
        result=merge(self.paths[::-1],launch_manifest=self.manifest_path,
              host_session=self.session_path,host_pins=self.pins_path,
              signed_host_reports=self.reports[::-1])
        self.assertEqual(result['outcome'],'PASS',result['harness_errors'])

    def test_incompatible_manifest_fails_even_with_signed_agent_bytes(self):
        doc=dict(self.manifest,target_url='http://127.0.0.1:1999')
        self.manifest_path.write_text(json.dumps(doc))
        result=self.check()
        self.assertEqual(result['outcome'],'FAIL')
        self.assertTrue(any('session' in s or 'launch' in s for s in result['harness_errors']))

    def test_missing_session_requires_fail_closed(self):
        result=self.check(host_session=self.root/'missing.json')
        self.assertEqual(result['outcome'],'FAIL')
        self.assertTrue(result['harness_errors'])

    def test_swapped_shard_roles_rejected(self):
        swapped=copy.deepcopy(self.session)
        swapped['host_assignments'].reverse()
        self.session_path.write_text(json.dumps(swapped))
        result=self.check()
        self.assertEqual(result['outcome'],'FAIL')

    def test_replay_from_old_challenge_rejected(self):
        self.session_path.write_text(json.dumps(create_session(self.manifest,self.pins,['host-0','host-1'])))
        result=self.check()
        self.assertEqual(result['outcome'],'FAIL')

    def test_missing_signed_shard_rejected(self):
        result=self.check(signed_host_reports=self.reports[:1])
        self.assertEqual(result['outcome'],'FAIL')

    def sample(self):
        a=1_000_000_000
        return {'local_send_ns':a,'remote_recv_ns':a+3_000_000,
                'remote_send_ns':a+4_000_000,'local_recv_ns':a+3_000_000,
                'local_elapsed_monotonic_ns':3_000_000}

    def clocks(self):
        paths=[];aggregated=[]
        for i in range(2):
            shard={'schema_version':1,'run_id':'testm7','shard_index':i,
                   'reference_id':'reference-test','samples':[self.sample() for _ in range(3)]}
            path=self.root/f'clock-{i}.json';path.write_text(json.dumps(shard))
            paths.append(path)
            aggregated.append({'shard_index':i,'samples':shard['samples']})
        combined={'schema_version':1,'run_id':'testm7','reference_id':'reference-test',
                  'shards':aggregated}
        aggr=self.root/'clock-total.json';aggr.write_text(json.dumps(combined))
        return paths,aggr

    def test_signed_clocks_bound_to_collected_aggregate(self):
        clocks,aggregate=self.clocks();self.sign(clocks)
        result=self.check(signed_clock_reports=clocks[::-1],clock_evidence=aggregate)
        self.assertEqual(result['outcome'],'PASS',result['harness_errors'])
        self.assertEqual(result['clock_evidence']['status'],'BOUNDED_SELF_REPORTED_NOT_AUTHENTICATED')

    def test_clock_aggregate_sample_tamper_detected(self):
        clocks,aggregate=self.clocks();self.sign(clocks)
        doc=json.loads(aggregate.read_text())
        doc['shards'][0]['samples'][0]['local_recv_ns']+=10
        aggregate.write_text(json.dumps(doc))
        result=self.check(signed_clock_reports=clocks,clock_evidence=aggregate)
        self.assertEqual(result['outcome'],'FAIL')
        self.assertTrue(any('signed clock aggregation' in s for s in result['harness_errors']))

    def test_orphan_signed_clock_without_aggregate_rejected(self):
        clocks,_=self.clocks();self.sign(clocks)
        self.assertEqual(self.check(signed_clock_reports=clocks)['outcome'],'FAIL')

    def test_unsigned_clock_aggregate_cannot_satisfy_signed_clock_requirement(self):
        clocks,aggregate=self.clocks();self.sign(clocks)
        self.assertEqual(self.check(signed_clock_reports=clocks[:1],clock_evidence=aggregate)['outcome'],'FAIL')

    def test_cli_merge_requires_session_and_keeps_run_results(self):
        cmd=[sys.executable,str(SIM/'distributed_merge.py'),
             '--launch-manifest',str(self.manifest_path),'--host-pins',str(self.pins_path),
             '--host-session',str(self.session_path),'--require-host-session',
             '--require-host-signatures']+[str(x) for x in self.paths]
        cmd[cmd.index('--host-session'):cmd.index('--host-session')]=[
             '--signed-host-report',str(self.reports[0]),'--signed-host-report',str(self.reports[1])]
        r=subprocess.run(cmd,capture_output=True,text=True,timeout=15)
        self.assertEqual(r.returncode,0,r.stderr)
        result=json.loads(r.stdout)
        self.assertEqual(result['session_sha256'],digest(self.session))

if __name__=='__main__':unittest.main()
