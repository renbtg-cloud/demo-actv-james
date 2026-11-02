"""Distributed shard merger: adversarial partial records and global assertions."""
import json
import pathlib
import shutil
import sys
import tempfile
import unittest
from types import SimpleNamespace
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from workload import create_plan
from distributed_merge import merge

PLAN={"profile":"steady","distribution":"sequential","duration_seconds":0.0,"seed":1729,
      "global_attempted_posts":12,"shared_keys":4,"conflict_percent":100,"target_qps":40.0,
      "shard_count":2,"start_at_ms":1793609000000,"ramp_start_rate":1.0,"burst_rate":100.0,
      "burst_width_seconds":1.0,"burst_period_seconds":5.0,"hotspot_percent":80.0,
      "hotspot_keys_percent":10.0,"skew_power":2.0}

class MergeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=pathlib.Path(self.temp.name)
        args=SimpleNamespace(requests=PLAN['global_attempted_posts'],keys=PLAN['shared_keys'],
            rate=PLAN['target_qps'],**{k:v for k,v in PLAN.items() if k not in
            ('global_attempted_posts','shared_keys','target_qps','shard_count','start_at_ms')})
        events=list(create_plan(args))
        for shard in range(2):
            folder=self.root/f'shard-{shard}';folder.mkdir()
            (folder/'agent-000.tsv').write_text('sequence\tslot\tvariant\tstatus\tlatency_ns\tid\terror\tsend_epoch_ms\tend_epoch_ms\tschedule_lag_ms\n'+
                ''.join(f'{seq}\t{slot}\t{variant}\t{202 if seq<4 else 409}\t1000000\t{("uuid-"+str(slot)) if seq<4 else ""}\t\t{1793609000000+offset}\t{1793609000010+offset}\t0\n' for seq,slot,variant,offset in events if seq%2==shard))
            data={'outcome':'SHARD_PASS_GLOBAL_AUDIT_PENDING','run_id':'testm7','target':'http://127.0.0.1:18081',
                'plan':{**PLAN,'shard_index':shard,'local_attempted_posts':6,'jvms':1},
                'observed':{'records':6,'post_attempts_per_second':20,'peak_inflight_http':1,'peak_jvms_with_inflight_http':1},
                'violations':[],'harness_errors':[],'state_verification_errors':[]}
            (folder/'summary.json').write_text(json.dumps(data))
        self.paths=[self.root/'shard-0',self.root/'shard-1']
    def tearDown(self):self.temp.cleanup()
    def test_complete_global_reconciliation(self):
        report=merge(self.paths)
        self.assertEqual(report['outcome'],'PASS',report)
        self.assertEqual(report['http_statuses'],{'202':4,'409':8})
        self.assertEqual(report['global_recorded_posts'],12)
        self.assertIsNone(report['global_throughput_qps'])
    def server_snapshot(self):
        return dict(schema_version=1,instance_id='test-jvm-one',run_id='testm7',
            server_elapsed_ns=6_000_000_000,first_start_elapsed_ns=2_000_000_000,
            last_finish_elapsed_ns=4_000_000_000,started=12,finished=12,
            inflight=0,peak_inflight=2,http_202=4,http_200=0,http_409=8,
            http_other=0,response_incomplete=0)
    def test_server_clock_can_measure_global_rate(self):
        report=merge(self.paths,server_metrics=self.server_snapshot())
        self.assertEqual(report['outcome'],'PASS',report)
        self.assertEqual(report['global_throughput_qps'],6.0)
        self.assertEqual(report['server_side']['server_handler_peak_inflight'],2)
        self.assertIsNone(report['global_peak_http_concurrency'])
    def test_server_missing_requests_fails_closed(self):
        m=self.server_snapshot();m['finished']-=1
        report=merge(self.paths,server_metrics=m)
        self.assertEqual(report['outcome'],'FAIL')
        self.assertTrue(any('counts disagree' in e for e in report['harness_errors']))
    def test_server_status_forgery_is_rejected(self):
        m=self.server_snapshot();m['http_202']+=1
        self.assertEqual(merge(self.paths,server_metrics=m)['outcome'],'FAIL')
    def test_server_wrong_run_is_rejected(self):
        m=self.server_snapshot();m['run_id']='other-run'
        self.assertEqual(merge(self.paths,server_metrics=m)['outcome'],'FAIL')
    def test_server_malformed_clock_is_rejected(self):
        m=self.server_snapshot();m['last_finish_elapsed_ns']=m['first_start_elapsed_ns']
        self.assertEqual(merge(self.paths,server_metrics=m)['outcome'],'FAIL')
    def test_server_incomplete_write_is_rejected(self):
        m=self.server_snapshot();m['response_incomplete']=1
        self.assertEqual(merge(self.paths,server_metrics=m)['outcome'],'FAIL')
    def test_missing_shard_cannot_pass(self):
        self.assertEqual(merge(self.paths[:1])['outcome'],'FAIL')
    def test_conflicting_configuration_rejected(self):
        p=self.paths[1]/'summary.json';d=json.loads(p.read_text());d['plan']['seed']+=1;p.write_text(json.dumps(d))
        with self.assertRaisesRegex(ValueError,'Incompatible'):merge(self.paths)
    def test_modified_payload_identity_rejected(self):
        p=self.paths[1]/'agent-000.tsv';rows=p.read_text().splitlines();columns=rows[1].split('\t');columns[1]='3';rows[1]='\t'.join(columns);p.write_text('\n'.join(rows)+'\n')
        report=merge(self.paths)
        self.assertEqual(report['outcome'],'FAIL')
        self.assertTrue(any('Global plan mismatch' in x for x in report['harness_errors']))
    def test_forged_duplicate_sequence_rejected(self):
        p=self.paths[1]/'agent-000.tsv';rows=p.read_text().splitlines();columns=rows[1].split('\t');columns[0]='0';rows[1]='\t'.join(columns);p.write_text('\n'.join(rows)+'\n')
        self.assertEqual(merge(self.paths)['outcome'],'FAIL')

if __name__=='__main__':unittest.main()
