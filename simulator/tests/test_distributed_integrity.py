"""Adversarial offline launch/index/merge checks, no network or fake multi-host claims."""
import json
import pathlib
import sys
import tempfile
import unittest
from types import SimpleNamespace
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
import distributed_prepare as dp
from distributed_integrity import verify_launch, verify_launch_scripts, seal_shards, verify_seal
from distributed_merge import merge, strict_external_preflight
import test_distributed_merge as base


class IntegrityTests(unittest.TestCase):
    def setUp(self):
        self.f = base.MergeTests('test_complete_global_reconciliation')
        self.f.setUp()
        self.paths=self.f.paths
        # A genuine v2 launcher manifest, parameter-compatible with old fixture.
        a=dp.parse_args(['--url','http://127.0.0.1:18081','--shards','2','--jvms-per-host','1',
            '--requests','12','--keys','4','--rate','40', '--conflict-percent','100',
            '--distribution','sequential','--run-id','testm7','--start-after-seconds','90',
            '--output-dir',str(self.f.root/'launch')])
        scripts,self.manifest=dp.build_scripts(a,now_ms=base.PLAN['start_at_ms']-90000)
        # Compatibility with original fixture's synthetic plan.
        self.assertEqual(self.manifest['launch_plan']['start_at_ms'],base.PLAN['start_at_ms'])
        self.launch=self.f.root/'launch';self.launch.mkdir()
        (self.launch/'manifest.json').write_text(json.dumps(self.manifest))
        for n,body in scripts.items():
            (self.launch/n).write_text(body)
        self.summary=[json.loads((p/'summary.json').read_text()) for p in self.paths]
        # The legacy fixture predates the per-agent resource fields now in real run summaries.
        for path,summary in zip(self.paths,self.summary):
            summary['plan']['heap_max_mb']=a.heap_max_mb
            summary['plan']['concurrency_per_jvm']=a.concurrency
            (path/'summary.json').write_text(json.dumps(summary))
    def tearDown(self):self.f.tearDown()
    def test_strict_external_gate_requires_all_four_sources(self):
        errors=strict_external_preflight()
        self.assertEqual(len(errors),4)
        self.assertEqual(strict_external_preflight(self.manifest,'trusted-index.json',
                                                  'trusted-clock.json',True),[])
        self.assertEqual(len(strict_external_preflight(self.manifest,'index.json',None,True)),1)
    def test_valid_manifest_and_scripts(self):
        self.assertEqual(verify_launch(self.manifest,self.summary),[])
        self.assertEqual(verify_launch_scripts(self.launch),[])
        self.assertEqual(merge(self.paths,launch_manifest=self.manifest)['outcome'],'PASS')
    def test_launcher_changed(self):
        (self.launch/'launch-host-000.sh').write_text('echo tampered\n')
        self.assertTrue(verify_launch_scripts(self.launch))
    def test_script_symlink_refused(self):
        f=self.launch/'launch-host-000.sh';f.unlink();f.symlink_to(self.launch/'launch-host-001.sh')
        self.assertTrue(verify_launch_scripts(self.launch))
    def test_manifest_wrong_identity_rejected(self):
        self.manifest['run_id']='unexpected'
        self.assertEqual(merge(self.paths,launch_manifest=self.manifest)['outcome'],'FAIL')
    def test_manifest_wrong_barrier_rejected(self):
        self.manifest['launch_plan']['start_at_ms']+=10
        self.assertEqual(merge(self.paths,launch_manifest=self.manifest)['outcome'],'FAIL')
    def test_manifest_wrong_allocation_rejected(self):
        self.manifest['jvms_per_host'][0]=2
        self.assertEqual(merge(self.paths,launch_manifest=self.manifest)['outcome'],'FAIL')
    def test_manifest_changed_burst_parameters_rejected(self):
        self.manifest['launch_plan']['burst_rate']+=10
        self.assertEqual(merge(self.paths,launch_manifest=self.manifest)['outcome'],'FAIL')
    def test_reverse_host_upload_order_does_not_relabel_shards(self):
        index=seal_shards(self.paths)
        self.assertEqual(index,seal_shards(list(reversed(self.paths))))
        self.assertEqual(verify_seal(index,list(reversed(self.paths))),[])
        self.assertEqual(merge(list(reversed(self.paths)),evidence_index=self._save(index),
                               launch_manifest=self.manifest)['outcome'],'PASS')
    def test_missing_duplicate_shard_index_denied(self):
        index=seal_shards(self.paths)
        data=json.loads((self.paths[1]/'summary.json').read_text())
        data['plan']['shard_index']=0
        (self.paths[1]/'summary.json').write_text(json.dumps(data))
        self.assertTrue(verify_seal(index,self.paths))
        with self.assertRaisesRegex(ValueError,'shard index'):
            seal_shards(self.paths)
    def test_valid_independent_shard_index(self):
        index=seal_shards(self.paths)
        self.assertEqual(verify_seal(index,self.paths),[])
        self.assertEqual(merge(self.paths,evidence_index=self._save(index))['outcome'],'PASS')
    def _save(self,data):
        p=self.f.root/'index.json';p.write_text(json.dumps(data));return p
    def test_index_detects_changed_agent_bytes(self):
        index=seal_shards(self.paths)
        p=self.paths[0]/'agent-000.tsv';p.write_bytes(p.read_bytes()+b'\n')
        self.assertTrue(verify_seal(index,self.paths))
        self.assertEqual(merge(self.paths,evidence_index=self._save(index))['outcome'],'FAIL')
    def test_index_detects_changed_summary(self):
        index=seal_shards(self.paths)
        p=self.paths[0]/'summary.json';p.write_bytes(p.read_bytes()+b' ')
        self.assertTrue(verify_seal(index,self.paths))
    def test_index_refuses_duplicate_member(self):
        index=seal_shards(self.paths);index['files'].append(dict(index['files'][0]))
        self.assertTrue(verify_seal(index,self.paths))
    def test_index_refuses_unexpected_agent(self):
        index=seal_shards(self.paths);(self.paths[0]/'agent-099.tsv').write_text('fake')
        self.assertTrue(verify_seal(index,self.paths))
    def test_index_refuses_symlink(self):
        index=seal_shards(self.paths)
        p=self.paths[0]/'agent-000.tsv';p.unlink();p.symlink_to(self.paths[1]/'agent-000.tsv')
        self.assertTrue(verify_seal(index,self.paths))
    def test_index_refuses_missing_shard(self):
        self.assertTrue(verify_seal(seal_shards(self.paths),self.paths[:1]))

if __name__=='__main__':unittest.main()
