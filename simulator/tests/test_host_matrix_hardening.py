"""Adversarial readiness collector tests: no claims of remote hardware."""
import copy
import json
import os
import pathlib
import stat
import subprocess
import sys
import tempfile
import time
import unittest

SIM=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SIM))
from distributed_prepare import build_scripts,parse_args
from host_matrix import audit,_read_host_report,_publish_verdict
from host_session import digest

class HardenedMatrixTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix='hard-matrix-')
        self.addCleanup(temp.cleanup)
        self.root=pathlib.Path(temp.name)
        self.now=int(time.time()*1000)
        args=parse_args(['--url','http://127.0.0.1:18081','--shards','2',
            '--jvms-per-host','1','--requests','8','--keys','2',
            '--run-id','hard-matrix','--start-after-seconds','180',
            '--output-dir',str(self.root/'unused')])
        launch,self.manifest=build_scripts(args,now_ms=self.now)
        from host_readiness import check
        self.docs=[]
        for i in range(2):
            path=self.root/f'launch-host-{i:03d}.sh'
            path.write_text(launch[path.name])
            d=check(self.manifest,i,path,now_ms=self.now)
            d['kernel_fingerprint']=str(i+1)*64
            self.docs.append(d)

    def strict(self,docs=None,manifest=None,**overrides):
        options={'now_ms':self.now,**overrides}
        return audit(manifest or self.manifest,docs or self.docs,
                     strict_temporal=True,**options)

    def test_accept_temporal_bound_and_record_limitations(self):
        result=self.strict()
        self.assertEqual(result['temporal_preflight'],'BOUNDED_SELF_REPORTED_TIMES_UNVERIFIED')
        self.assertIn('does not authenticate',result['identity_note'])

    def test_reject_stale_self_report(self):
        docs=copy.deepcopy(self.docs)
        docs[0]['observed_epoch_ms']-=300001
        with self.assertRaisesRegex(ValueError,'too old'):self.strict(docs)

    def test_reject_future_self_report(self):
        docs=copy.deepcopy(self.docs)
        docs[0]['observed_epoch_ms']+=10001
        with self.assertRaisesRegex(ValueError,'future'):self.strict(docs)

    def test_reject_expired_launch_barrier(self):
        with self.assertRaisesRegex(ValueError,'Launch barrier'):self.strict(now_ms=self.now+151000)

    def test_reject_conflicting_barrier_fields(self):
        manifest=copy.deepcopy(self.manifest)
        manifest['launch_plan']['start_at_ms']+=1
        docs=copy.deepcopy(self.docs)
        for item in docs:item['manifest_sha256']=digest(manifest)
        with self.assertRaisesRegex(ValueError,'Contradictory launch barrier'):self.strict(docs,manifest)

    def test_reject_report_after_barrier_guard(self):
        docs=copy.deepcopy(self.docs)
        manifest=copy.deepcopy(self.manifest)
        manifest['start_at_epoch_ms']=self.now+60000
        manifest['launch_plan']['start_at_ms']=self.now+60000
        docs[1]['observed_epoch_ms']=self.now+31000
        for item in docs:item['manifest_sha256']=digest(manifest)
        with self.assertRaisesRegex(ValueError,'too close'):self.strict(docs,manifest,max_future_ms=60000)

    def test_reject_invalid_policy_bool_and_large_budget(self):
        for options in ({'max_age_ms':True},{'max_future_ms':60001},{'max_age_ms':0}):
            with self.subTest(options=options):
                with self.assertRaisesRegex(ValueError,'budget'):self.strict(**options)

    def test_canonical_commitments_independent_of_arrival_order(self):
        forward=audit(self.manifest,self.docs)
        reverse=audit(self.manifest,self.docs[::-1])
        self.assertEqual(forward['report_commitments'],reverse['report_commitments'])
        self.assertEqual(forward['report_commitments'][0]['canonical_sha256'],digest(self.docs[0]))
        altered=copy.deepcopy(self.docs)
        altered[1]['free_disk_bytes_at_check']+=1
        self.assertNotEqual(forward['report_commitments'],audit(self.manifest,altered)['report_commitments'])

    def test_detect_nested_duplicate_json_keys(self):
        target=self.root/'bad.json'
        target.write_text('{"a":{"hello":1,"hello":2}}')
        with self.assertRaisesRegex(ValueError,'Duplicate JSON'):_read_host_report(target)

    def test_reject_symlink_report(self):
        target=self.root/'report.json';target.write_text(json.dumps(self.docs[0]))
        link=self.root/'link.json';link.symlink_to(target)
        with self.assertRaises(OSError):_read_host_report(link)

    def test_reject_fifo_without_blocking(self):
        fifo=self.root/'hanging-fifo';os.mkfifo(fifo)
        with self.assertRaisesRegex(ValueError,'Unsafe'):_read_host_report(fifo)

    def test_reject_oversized_file_and_nan(self):
        oversized=self.root/'oversized.json';oversized.write_bytes(b'a'*131073)
        with self.assertRaisesRegex(ValueError,'oversized'):_read_host_report(oversized)
        invalid=self.root/'invalid.json';invalid.write_text('{"a":NaN}')
        with self.assertRaisesRegex(ValueError,'constant'):_read_host_report(invalid)

    def test_published_result_complete_private_and_immutable(self):
        output=self.root/'result.json';result=self.strict()
        _publish_verdict(output,result)
        self.assertEqual(json.loads(output.read_text()),result)
        self.assertEqual(stat.S_IMODE(output.stat().st_mode),0o600)
        with self.assertRaises(FileExistsError):_publish_verdict(output,{'status':'UNTRUSTED'})
        self.assertEqual(json.loads(output.read_text()),result)
        self.assertEqual(list(self.root.glob('.*.tmp')),[])

    def test_existing_symlink_never_overwritten(self):
        existing=self.root/'protected';existing.write_text('KEEP')
        dest=self.root/'result';dest.symlink_to(existing)
        with self.assertRaises(FileExistsError):_publish_verdict(dest,{'status':'UNTRUSTED'})
        self.assertEqual(existing.read_text(),'KEEP')

    def test_cli_fail_closed_no_result_published(self):
        manifest=self.root/'manifest.json';manifest.write_text(json.dumps(self.manifest))
        docs=[]
        for i,item in enumerate(self.docs):
            file=self.root/f'report-{i}.json';file.write_text(json.dumps(item));docs.append(str(file))
        result=self.root/'verdict.json'
        proc=subprocess.run([sys.executable,str(SIM/'host_matrix.py'),
            '--manifest',str(manifest),'--strict-temporal','--max-age-ms','999',
            '--output',str(result),*docs],capture_output=True,text=True,timeout=10)
        self.assertEqual(proc.returncode,2)
        self.assertFalse(result.exists())
        self.assertIn('REJECTED',proc.stderr)

if __name__=='__main__':unittest.main()
