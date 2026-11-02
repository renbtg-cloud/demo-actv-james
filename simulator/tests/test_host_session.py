"""Adversarial tests: collector-run challenge, strict shard roles, signed clocks."""
import copy
import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

SIM=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SIM))
from host_session import create_session,validate,digest,read_json
from host_signed_evidence import make_envelope,verify_envelopes
from collector_ledger import record_once


class SessionTests(unittest.TestCase):
    def setUp(self):
        from test_host_signed_evidence import SignedEvidenceTests
        self.fixture=SignedEvidenceTests('test_two_real_signing_keys_verified')
        self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.manifest={'schema_version':2,'run_id':'run-CI-01','shards':2,
                       'target_url':'http://127.0.0.1:8811', 'shared_keys':4}
        self.pins=self.fixture.pins
        self.session=create_session(self.manifest,self.pins,['host-0','host-1'])
        self.reports=[]
        for i,folder in enumerate(self.fixture.folders):
            report=pathlib.Path(self.fixture.tmp.name)/f'bound-{i}.json'
            report.write_text(json.dumps(make_envelope(folder,'run-CI-01',f'host-{i}',i,
                self.fixture.key_paths[i],session=self.session,manifest=self.manifest,pins=self.pins)))
            self.reports.append(report)

    def verify(self,session=None,pins=None,manifest=None,reports=None,clock_reports=None):
        return verify_envelopes(pins if pins is not None else self.pins,
              reports if reports is not None else self.reports,self.fixture.folders,
              'run-CI-01',session=self.session if session is None else session,
              manifest=self.manifest if manifest is None else manifest,clock_reports=clock_reports)

    def test_all_signature_hosts_session_bound(self):
        result,errors=self.verify()
        self.assertEqual(errors,[])
        self.assertEqual(len(result['shards']),2)

    def test_nonce_has_full_256_bits_and_is_randomized(self):
        self.assertEqual(len(self.session['nonce']),64)
        self.assertNotEqual(self.session['nonce'],create_session(self.manifest,self.pins,['host-0','host-1'])['nonce'])

    def test_reject_replayed_signature_for_different_fresh_session(self):
        other=create_session(self.manifest,self.pins,['host-0','host-1'])
        self.assertTrue(self.verify(session=other)[1])

    def test_reject_swapped_host_roles_even_with_valid_signatures(self):
        altered=copy.deepcopy(self.session)
        altered['host_assignments'].reverse()
        self.assertTrue(self.verify(session=altered)[1])

    def test_reject_replaced_manifest_and_pins(self):
        altered=dict(self.manifest,target_url='http://127.0.0.1:8812')
        self.assertTrue(self.verify(manifest=altered)[1])
        changed=dict(self.pins)
        changed['host-0']=self.pins['host-1'];changed['host-1']=self.pins['host-0']
        self.assertTrue(self.verify(pins=changed)[1])

    def test_reject_unsigned_legacy_envelope_downgrade(self):
        self.assertTrue(verify_envelopes(self.pins,self.fixture.reports,self.fixture.folders,
                                           'run-CI-01',session=self.session,manifest=self.manifest)[1])
        self.assertTrue(verify_envelopes(self.pins,self.reports,self.fixture.folders,'run-CI-01')[1])

    def test_reject_session_with_noncanonical_nonce(self):
        for bad in ('f'*63,'F'*64,'gg'*32,0):
            doc=dict(self.session,nonce=bad)
            with self.assertRaises(ValueError):validate(doc,self.manifest,self.pins)

    def test_reject_duplicate_or_unassigned_host_roles(self):
        doc=dict(self.session,host_assignments=['host-0','host-0'])
        with self.assertRaises(ValueError):validate(doc,self.manifest,self.pins)
        doc=dict(self.session,host_assignments=['host-0'])
        with self.assertRaises(ValueError):validate(doc,self.manifest,self.pins)

    def test_reject_signed_summary_tamper(self):
        p=self.fixture.folders[1]/'summary.json';p.write_bytes(p.read_bytes()+b'\n')
        self.assertTrue(self.verify()[1])

    def test_signing_wrong_shard_identity_rejected_before_signature(self):
        with self.assertRaises(ValueError):
            make_envelope(self.fixture.folders[1],'run-CI-01','host-0',1,
                          self.fixture.key_paths[0],session=self.session,manifest=self.manifest,pins=self.pins)

    def test_signing_clock_evidence_bound_and_tamper_detected(self):
        clocks=[];signed=[]
        for i,folder in enumerate(self.fixture.folders):
            c=pathlib.Path(self.fixture.tmp.name)/f'clock-{i}.json'
            c.write_text(json.dumps({'schema_version':1,'run_id':'run-CI-01','shard_index':i,'samples':[1,2,3]}))
            clocks.append(c)
            r=pathlib.Path(self.fixture.tmp.name)/f'clockbound-{i}.json'
            r.write_text(json.dumps(make_envelope(folder,'run-CI-01',f'host-{i}',i,
                self.fixture.key_paths[i],session=self.session,manifest=self.manifest,pins=self.pins,clock_report=c)))
            signed.append(r)
        result,err=self.verify(reports=signed,clock_reports=clocks[::-1])
        self.assertEqual(err,[],err)
        clocks[0].write_bytes(clocks[0].read_bytes()+b'\n')
        self.assertTrue(self.verify(reports=signed,clock_reports=clocks)[1])

    def test_missing_clock_report_or_injected_duplicate_fails(self):
        c=pathlib.Path(self.fixture.tmp.name)/'clock.json'
        c.write_text(json.dumps({'schema_version':1,'run_id':'run-CI-01','shard_index':0}))
        self.assertTrue(self.verify(clock_reports=[c,c])[1])
        self.assertTrue(self.verify(clock_reports=[c])[1])

    def test_collector_ledger_one_time_acceptance_and_replay(self):
        db=pathlib.Path(self.fixture.tmp.name)/'collector.db'
        merge={'outcome':'PASS','run_id':'run-CI-01','session_sha256':digest(self.session),
            'signed_host_evidence':{'status':'ALL_PINNED_SIGNING_KEYS_VERIFIED_NOT_HOST_ATTESTATION'}}
        self.assertEqual(record_once(db,self.session,merge)['status'],'COLLECTOR_SESSION_CONSUMED_ONCE')
        with self.assertRaisesRegex(ValueError,'replay'):
            record_once(db,self.session,merge)
        with self.assertRaises(ValueError):record_once(db,self.session,dict(merge,outcome='FAIL'))

    def test_ledger_rejects_synthetic_pass_without_matching_digest(self):
        db=pathlib.Path(self.fixture.tmp.name)/'collector2.db'
        merge={'outcome':'PASS','run_id':'run-CI-01','session_sha256':'0'*64,
            'signed_host_evidence':{'status':'ALL_PINNED_SIGNING_KEYS_VERIFIED_NOT_HOST_ATTESTATION'}}
        with self.assertRaises(ValueError):record_once(db,self.session,merge)
        self.assertFalse(db.exists())

    def test_real_cli_session_creation_and_no_overwrite(self):
        base=pathlib.Path(self.fixture.tmp.name)
        m=base/'manifest.json';m.write_text(json.dumps(self.manifest))
        keys=base/'keys.json';keys.write_text(json.dumps(self.pins))
        out=base/'session.json'
        cmd=[sys.executable,str(SIM/'host_session.py'),'--manifest',str(m),
             '--pins',str(keys),'--hosts','host-0,host-1','--output',str(out)]
        p=subprocess.run(cmd,capture_output=True,text=True,timeout=10)
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertEqual(validate(read_json(out),self.manifest,self.pins),digest(read_json(out)))
        p=subprocess.run(cmd,capture_output=True,text=True,timeout=10)
        self.assertNotEqual(p.returncode,0)

    def test_real_cli_sign_and_verify_session(self):
        base=pathlib.Path(self.fixture.tmp.name)
        m=base/'manifest.json';m.write_text(json.dumps(self.manifest))
        pins=base/'pins.json';pins.write_text(json.dumps(self.pins))
        session=base/'session.json';session.write_text(json.dumps(self.session))
        reports=[]
        for i,folder in enumerate(self.fixture.folders):
            outfile=base/f'cli-signed-{i}.json'
            cmd=[sys.executable,str(SIM/'host_signed_evidence.py'),'sign','--folder',str(folder),
                 '--run-id','run-CI-01','--host-id',f'host-{i}','--shard',str(i),
                 '--private-key',str(self.fixture.key_paths[i]),'--output',str(outfile),
                 '--session',str(session),'--manifest',str(m),'--pins',str(pins)]
            p=subprocess.run(cmd,capture_output=True,text=True,timeout=10)
            self.assertEqual(p.returncode,0,p.stderr)
            reports.append(outfile)
        cmd=[sys.executable,str(SIM/'host_signed_evidence.py'),'verify',
              '--pins',str(pins),'--run-id','run-CI-01', '--reports',*(str(x) for x in reports),
              '--folders',*(str(x) for x in self.fixture.folders),
              '--session',str(session),'--manifest',str(m)]
        p=subprocess.run(cmd,capture_output=True,text=True,timeout=10)
        self.assertEqual(p.returncode,0,p.stderr)

if __name__=='__main__':unittest.main()
