"""Auditing shard-indexed self-reported multi-host preflight inventory."""
import copy
import json
import pathlib
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
SIM=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SIM))
from distributed_prepare import parse_args,build_scripts
from host_readiness import check
from host_matrix import audit

class MatrixTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory(prefix='host-matrix-');self.addCleanup(t.cleanup)
        self.root=pathlib.Path(t.name);self.now=int(time.time()*1000)
        args=parse_args(['--url','http://127.0.0.1:18081','--shards','2',
                         '--jvms-per-host','1','--requests','8','--keys','2',
                         '--run-id','matrix-test','--output-dir',str(self.root/'output')])
        scripts,self.manifest=build_scripts(args,now_ms=self.now)
        self.reports=[]
        for i in range(2):
            launcher=self.root/f'launch-host-{i:03d}.sh'
            launcher.write_text(scripts[launcher.name])
            self.reports.append(check(self.manifest,i,launcher,now_ms=self.now))
        self.reports[0]['kernel_fingerprint']='a'*64
        self.reports[1]['kernel_fingerprint']='b'*64

    def test_accept_distinct_self_reported_kernels_without_physical_claim(self):
        verdict=audit(self.manifest,self.reports[::-1])
        self.assertIn('UNVERIFIED',verdict['status'])
        self.assertFalse(verdict['network_probed_for_all'])

    def test_reject_same_kernel_on_real_single_host(self):
        self.reports[1]['kernel_fingerprint']=self.reports[0]['kernel_fingerprint']
        with self.assertRaisesRegex(ValueError,'SAME_KERNEL'):
            audit(self.manifest,self.reports)

    def test_reject_replayed_different_plan(self):
        other=copy.deepcopy(self.manifest);other['shared_keys']=other['shared_keys']+1
        with self.assertRaisesRegex(ValueError,'manifest'):
            audit(other,self.reports)

    def test_reject_duplicate_shards(self):
        self.reports[1]['shard_index']=0
        with self.assertRaisesRegex(ValueError,'Duplicate'):
            audit(self.manifest,self.reports)

    def test_reject_claimed_no_probe_when_required(self):
        with self.assertRaisesRegex(ValueError,'probe'):
            audit(self.manifest,self.reports,require_network=True)

    def test_reject_modified_java_heap(self):
        self.reports[0]['configured_java_heap_total_bytes']=1
        with self.assertRaisesRegex(ValueError,'runtime'):
            audit(self.manifest,self.reports)

    def test_reject_unverified_fingerprint(self):
        self.reports[0]['kernel_fingerprint']=None
        with self.assertRaisesRegex(ValueError,'fingerprint'):
            audit(self.manifest,self.reports)

    def test_reject_extra_report_field(self):
        self.reports[0]['unrecognized']='attack'
        with self.assertRaisesRegex(ValueError,'Malformed'):
            audit(self.manifest,self.reports)

    def test_reject_short_disk(self):
        self.reports[0]['free_disk_bytes_at_check']=1
        with self.assertRaisesRegex(ValueError,'resource'):
            audit(self.manifest,self.reports)

    def test_missing_shard_refused(self):
        with self.assertRaisesRegex(ValueError,'exactly one'):
            audit(self.manifest,self.reports[:1])

    def test_two_real_separate_cli_preflights_on_one_kernel_must_fail_isolation(self):
        # Two independent Python OS processes and two real Java-version probes;
        # this is deliberately NOT a two-machine test.
        import subprocess
        (self.root/'manifest.json').write_text(json.dumps(self.manifest))
        for i in range(2):
            launcher=self.root/f'launch-host-{i:03d}.sh'
            report=self.root/f'actual-{i}.json'
            cmd=[sys.executable,str(SIM/'host_readiness.py'),
                 '--manifest',str(self.root/'manifest.json'),
                 '--shard-index',str(i),'--launcher',str(launcher),
                 '--output',str(report)]
            child=subprocess.run(cmd,capture_output=True,text=True,timeout=12)
            self.assertEqual(child.returncode,0,child.stderr)
        a=json.loads((self.root/'actual-0.json').read_text())
        b=json.loads((self.root/'actual-1.json').read_text())
        self.assertEqual(a['kernel_fingerprint'],b['kernel_fingerprint'])
        with self.assertRaisesRegex(ValueError,'SAME_KERNEL_OBSERVED'):
            audit(self.manifest,[self.root/'actual-0.json',self.root/'actual-1.json'])

    def test_json_input_and_dedicated_cli(self):
        import subprocess
        (self.root/'manifest.json').write_text(json.dumps(self.manifest))
        paths=[]
        for i,item in enumerate(self.reports):
            p=self.root/f'preflight-{i}.json';p.write_text(json.dumps(item));paths.append(p)
        cmd=[sys.executable,str(SIM/'host_matrix.py'),'--manifest',str(self.root/'manifest.json'),
             '--output',str(self.root/'result.json'),str(paths[1]),str(paths[0])]
        run=subprocess.run(cmd,capture_output=True,text=True,timeout=8)
        self.assertEqual(run.returncode,0,run.stderr)
        self.assertIn('UNVERIFIED',run.stdout)
        self.assertEqual(json.loads((self.root/'result.json').read_text())['shards'],2)

if __name__=='__main__':unittest.main()
