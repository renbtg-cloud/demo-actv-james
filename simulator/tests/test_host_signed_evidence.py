"""Real Ed25519 signatures, independently pinned host keys, tamper/replay rejection."""
import base64
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

SIM=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SIM))
from host_signed_evidence import make_envelope,verify_envelopes


class SignedEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='signed-host-')
        self.addCleanup(self.tmp.cleanup)
        root=pathlib.Path(self.tmp.name)
        self.folders=[];self.key_paths=[];self.reports=[];self.pins={}
        for i in range(2):
            folder=root/f'host-{i}';folder.mkdir()
            (folder/'summary.json').write_text(json.dumps({'run_id':'run-CI-01','plan':{'shard_index':i,'jvms':2}}))
            (folder/'agent-000.tsv').write_text('request,ok\n1,1\n')
            (folder/'agent-001.tsv').write_text('request,ok\n2,1\n')
            private=Ed25519PrivateKey.generate()
            path=root/f'key-{i}.pem'
            path.write_bytes(private.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
            path.chmod(0o600)
            self.pins[f'host-{i}']=private.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode()
            report=root/f'report-{i}.json'
            report.write_text(json.dumps(make_envelope(folder,'run-CI-01',f'host-{i}',i,path)))
            self.folders.append(folder);self.key_paths.append(path);self.reports.append(report)

    def check(self,pins=None, reports=None, folders=None, run_id='run-CI-01'):
        return verify_envelopes(pins or self.pins,reports or self.reports,folders or self.folders,run_id)

    def test_two_real_signing_keys_verified(self):
        result,errors=self.check()
        self.assertFalse(errors,errors)
        self.assertEqual(result['status'],'ALL_PINNED_SIGNING_KEYS_VERIFIED_NOT_HOST_ATTESTATION')
        self.assertEqual([x['shard'] for x in result['shards']],[0,1])

    def test_reject_agent_tampering(self):
        (self.folders[0]/'agent-000.tsv').write_text('request,ok\n1,0\n')
        self.assertTrue(self.check()[1])

    def test_reject_summary_tampering(self):
        (self.folders[1]/'summary.json').write_text('{"run_id":"other","plan":{"shard_index":1,"jvms":2}}')
        self.assertTrue(self.check()[1])

    def test_reject_signature_mutation(self):
        report=json.loads(self.reports[0].read_text())
        sig=bytearray(base64.b64decode(report['signature']));sig[5]^=0x01
        report['signature']=base64.b64encode(sig).decode()
        self.reports[0].write_text(json.dumps(report))
        self.assertTrue(any('Signature invalid' in s for s in self.check()[1]))

    def test_invalid_base64_is_rejected_as_a_validation_result(self):
        data=json.loads(self.reports[0].read_text())
        data['signature']='not valid base64!'
        self.reports[0].write_text(json.dumps(data))
        result,errors=self.check()
        self.assertIsNone(result)
        self.assertTrue(errors)

    def test_malformed_summary_is_rejected_as_a_validation_result(self):
        (self.folders[0]/'summary.json').write_text('[]')
        result,errors=self.check()
        self.assertIsNone(result)
        self.assertTrue(errors)

    def test_reject_replayed_run(self):
        self.assertTrue(self.check(run_id='run-another')[1])

    def test_reject_swapped_public_key(self):
        pins=dict(self.pins);pins['host-0'],pins['host-1']=pins['host-1'],pins['host-0']
        self.assertTrue(self.check(pins=pins)[1])

    def test_reject_reused_key_for_two_pinned_hosts(self):
        pins=dict(self.pins);pins['host-1']=pins['host-0']
        self.assertTrue(any('reused' in s for s in self.check(pins=pins)[1]))

    def test_reject_reused_host_identity(self):
        report=json.loads(self.reports[1].read_text())
        report['payload']['host_id']='host-0'
        self.reports[1].write_text(json.dumps(report))
        self.assertTrue(self.check()[1])

    def test_accept_reversed_arrival_order_but_reject_missing_shard(self):
        result,errors=self.check(reports=self.reports[::-1],folders=self.folders[::-1])
        self.assertEqual(errors,[])
        self.assertEqual([x['shard'] for x in result['shards']],[0,1])
        self.assertTrue(self.check(reports=[self.reports[0]]*2)[1])

    def test_reject_unprotected_private_key_permissions(self):
        self.key_paths[0].chmod(0o644)
        with self.assertRaisesRegex(ValueError,'owner-only'):
            make_envelope(self.folders[0],'run-CI-01','host-0',0,self.key_paths[0])

    def test_real_cli_verify_and_refuse_overwrite(self):
        root=pathlib.Path(self.tmp.name)
        (root/'pins.json').write_text(json.dumps(self.pins))
        cmd=[sys.executable,str(SIM/'host_signed_evidence.py'),'verify','--pins',str(root/'pins.json'),
             '--run-id','run-CI-01','--reports',*(str(p) for p in self.reports),
             '--folders',*(str(p) for p in self.folders)]
        ok=subprocess.run(cmd,capture_output=True,text=True,timeout=10)
        self.assertEqual(ok.returncode,0,ok.stderr)
        self.assertIn('ALL_PINNED_SIGNING_KEYS_VERIFIED_NOT_HOST_ATTESTATION',ok.stdout)
        destination=root/'should-refuse.json'
        destination.write_text('original')
        bad=subprocess.run([sys.executable,str(SIM/'host_signed_evidence.py'),'sign',
                            '--folder',str(self.folders[0]),'--run-id','run-CI-01',
                            '--host-id','host-0','--shard','0','--private-key',str(self.key_paths[0]),
                            '--output',str(destination)],capture_output=True,text=True,timeout=10)
        self.assertNotEqual(bad.returncode,0)
        self.assertEqual(destination.read_text(),'original')




class SignedMergeIntegrationTests(unittest.TestCase):
    def setUp(self):
        import test_distributed_merge as base
        self.fixture=base.MergeTests('test_complete_global_reconciliation')
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.paths=self.fixture.paths
        self.pins={};self.reports=[]
        for i,folder in enumerate(self.paths):
            private=Ed25519PrivateKey.generate()
            keypath=self.fixture.root/f'host-key-{i}.pem'
            keypath.write_bytes(private.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
            keypath.chmod(0o600)
            self.pins[f'host-{i}']=private.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode()
            target=self.fixture.root/f'signed-{i}.json'
            target.write_text(json.dumps(make_envelope(folder,'testm7',f'host-{i}',i,keypath)))
            self.reports.append(target)
        self.inventory=self.fixture.root/'pins.json'
        self.inventory.write_text(json.dumps(self.pins))

    def test_valid_pinned_signatures_integrate_with_global_merge(self):
        from distributed_merge import merge
        result=merge(self.paths,host_pins=self.inventory,signed_host_reports=self.reports)
        self.assertEqual(result['outcome'],'PASS',result['harness_errors'])
        self.assertEqual(result['signed_host_evidence']['status'],
                         'ALL_PINNED_SIGNING_KEYS_VERIFIED_NOT_HOST_ATTESTATION')
        self.assertIsNone(result['global_throughput_qps'])

    def test_any_unsigned_shard_fails_closed(self):
        from distributed_merge import merge
        result=merge(self.paths,host_pins=self.inventory,signed_host_reports=self.reports[:1])
        self.assertEqual(result['outcome'],'FAIL')
        self.assertTrue(any('signed host evidence' in s for s in result['harness_errors']))

    def test_tampered_agent_fails_global_merge(self):
        from distributed_merge import merge
        target=self.paths[0]/'agent-000.tsv';target.write_bytes(target.read_bytes()+b'\n')
        result=merge(self.paths,host_pins=self.inventory,signed_host_reports=self.reports)
        self.assertEqual(result['outcome'],'FAIL')
        self.assertTrue(any('signed host evidence' in s for s in result['harness_errors']))

    def test_one_sided_signature_options_fail_closed(self):
        from distributed_merge import merge
        result=merge(self.paths,host_pins=self.inventory)
        self.assertEqual(result['outcome'],'FAIL')
        self.assertTrue(any('requires BOTH' in s for s in result['harness_errors']))

if __name__=='__main__':unittest.main()
