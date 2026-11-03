"""Signed preflight/post-run audit uses independent real-key signatures."""
import copy
import json
import pathlib
import subprocess
import sys
import unittest

SIM=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SIM))
from host_readiness import check
from host_readiness_signature import make_proof
from host_matrix import audit
from host_session import digest
from distributed_merge import merge

class HandoffTests(unittest.TestCase):
    def setUp(self):
        from test_host_session_merge import LiveCollectorContractTests
        self.lab=LiveCollectorContractTests('test_valid_complete_merger_with_bound_signed_session')
        self.lab.setUp();self.addCleanup(self.lab.doCleanups)
        self.root=self.lab.root
        self.report_paths=[];self.proof_paths=[]
        self.now=self.lab.manifest['start_at_epoch_ms']-90000
        for i in range(2):
            doc=check(self.lab.manifest,i,self.lab.fixture.launch/f'launch-host-{i:03d}.sh',now_ms=self.now)
            doc['kernel_fingerprint']=str(i+1)*64
            report=self.root/f'preflight-{i}.json';report.write_text(json.dumps(doc,sort_keys=True)+'\n')
            self.report_paths.append(report)
            proof=make_proof(report,self.lab.keys[i],f'host-{i}',
                             self.lab.session,self.lab.manifest,self.lab.pins)
            p=self.root/f'preflight-proof-{i}.json';p.write_text(json.dumps(proof,sort_keys=True)+'\n')
            self.proof_paths.append(p)
            summary_file=self.lab.paths[i]/'summary.json'
            summary=json.loads(summary_file.read_text())
            summary['host_environment']={'schema_version':1,'kernel_fingerprint':doc['kernel_fingerprint']}
            summary_file.write_text(json.dumps(summary))
        self.lab.sign()  # Re-sign the actual modified shard summaries with real Ed25519 keys.
        self.matrix=self.root/'signed-matrix.json'
        self.republish()

    def republish(self):
        result=audit(self.lab.manifest,self.report_paths,strict_temporal=True,now_ms=self.now,
                     signed_readiness_paths=self.proof_paths,session=self.lab.session,pins=self.lab.pins)
        self.matrix.write_text(json.dumps(result,sort_keys=True)+'\n')

    def result(self,**override):
        options={'launch_manifest':self.lab.manifest_path,'host_pins':self.lab.pins_path,
                 'host_session':self.lab.session_path,'signed_host_reports':self.lab.reports,
                 'readiness_matrix':self.matrix,'readiness_reports':self.report_paths,
                 'signed_readiness':self.proof_paths}
        options.update(override)
        return merge(self.lab.paths,**options)

    def test_real_signed_handoff_bound_to_two_provenance_chains(self):
        r=self.result()
        self.assertEqual(r['outcome'],'PASS',r['harness_errors'])
        h=r['readiness_handoff']
        self.assertEqual(h['status'],'SIGNED_PREFLIGHT_AND_POSTRUN_IDENTITIES_AGREE_NOT_PHYSICAL_ATTESTATION')
        self.assertEqual(h['session_sha256'],digest(self.lab.session))
        self.assertEqual([x['shard_index'] for x in h['host_commitments']],[0,1])

    def test_reverse_arrival_order_does_not_change_handoff_commitments(self):
        before=self.result()['readiness_handoff']
        after=merge(self.lab.paths[::-1],launch_manifest=self.lab.manifest_path,
                    host_pins=self.lab.pins_path,host_session=self.lab.session_path,
                    signed_host_reports=self.lab.reports[::-1],readiness_matrix=self.matrix,
                    readiness_reports=self.report_paths[::-1],signed_readiness=self.proof_paths[::-1])
        self.assertEqual(after['outcome'],'PASS',after['harness_errors'])
        self.assertEqual(before,after['readiness_handoff'])

    def test_tampered_preflight_verdict_rejected(self):
        doc=json.loads(self.matrix.read_text());doc['network_probed_for_all']=True
        self.matrix.write_text(json.dumps(doc))
        self.assertEqual(self.result()['outcome'],'FAIL')

    def test_rewritten_temporal_reference_rejected(self):
        doc=json.loads(self.matrix.read_text());doc['collector_checked_at_epoch_ms']+=999999
        self.matrix.write_text(json.dumps(doc))
        self.assertEqual(self.result()['outcome'],'FAIL')

    def test_forged_time_budget_rejected(self):
        doc=json.loads(self.matrix.read_text());doc['temporal_budgets']['max_age_ms']=True
        self.matrix.write_text(json.dumps(doc))
        self.assertEqual(self.result()['outcome'],'FAIL')

    def test_unsigned_matrix_does_not_qualify(self):
        doc=json.loads(self.matrix.read_text());doc['signed_readiness']={'status':'NOT_REQUESTED'}
        self.matrix.write_text(json.dumps(doc))
        self.assertEqual(self.result()['outcome'],'FAIL')

    def test_preflight_modified_bytes_refused(self):
        self.report_paths[0].write_bytes(self.report_paths[0].read_bytes()+b'\n')
        result=self.result()
        self.assertEqual(result['outcome'],'FAIL')
        self.assertTrue(any('signed readiness handoff' in x for x in result['harness_errors']))

    def test_signed_proof_modified_refused(self):
        proof=json.loads(self.proof_paths[1].read_text());proof['payload']['session_sha256']='0'*64
        self.proof_paths[1].write_text(json.dumps(proof))
        self.assertEqual(self.result()['outcome'],'FAIL')

    def test_postrun_signed_kernel_mismatch_rejected(self):
        target=self.lab.paths[1]/'summary.json';doc=json.loads(target.read_text())
        doc['host_environment']['kernel_fingerprint']='a'*64
        target.write_text(json.dumps(doc))
        self.lab.sign()  # Even a genuinely re-signed but contradictory report must fail.
        self.assertEqual(self.result()['outcome'],'FAIL')
        self.assertIn('kernel',str(self.result()['harness_errors']))

    def test_missing_one_preflight_proof_is_failure_not_silent_downgrade(self):
        r=self.result(signed_readiness=self.proof_paths[:1])
        self.assertEqual(r['outcome'],'FAIL')
        self.assertIsNone(r['readiness_handoff'])

    def test_only_one_handoff_argument_is_failure(self):
        r=self.result(readiness_matrix=None)
        self.assertEqual(r['outcome'],'FAIL')
        self.assertTrue(any('COMPLETE' in x for x in r['harness_errors']))

    def test_wrong_collector_session_refused(self):
        from host_session import create_session
        next_session=create_session(self.lab.manifest,self.lab.pins,['host-0','host-1'])
        self.lab.session_path.write_text(json.dumps(next_session))
        self.assertEqual(self.result()['outcome'],'FAIL')

    def test_trusted_preflight_symlink_refused(self):
        dest=self.root/'linked';dest.symlink_to(self.matrix)
        self.assertEqual(self.result(readiness_matrix=dest)['outcome'],'FAIL')

    def test_actual_merge_cli_with_signed_handoff(self):
        cmd=[sys.executable,str(SIM/'distributed_merge.py'),
             '--launch-manifest',str(self.lab.manifest_path),
             '--host-pins',str(self.lab.pins_path),'--host-session',str(self.lab.session_path),
             '--signed-host-report',str(self.lab.reports[0]),'--signed-host-report',str(self.lab.reports[1]),
             '--readiness-matrix',str(self.matrix),
             '--readiness-report',str(self.report_paths[0]),'--readiness-report',str(self.report_paths[1]),
             '--signed-readiness',str(self.proof_paths[0]),'--signed-readiness',str(self.proof_paths[1]),
             str(self.lab.paths[0]),str(self.lab.paths[1])]
        p=subprocess.run(cmd,capture_output=True,text=True,timeout=15)
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertEqual(json.loads(p.stdout)['readiness_handoff']['session_sha256'],digest(self.lab.session))

if __name__=='__main__':unittest.main()
