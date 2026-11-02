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
    def multi_snapshots(self):
        a=self.server_snapshot()
        b=self.server_snapshot()
        a.update(instance_id='service-A',started=5,finished=5,peak_inflight=2,http_202=2,http_409=3)
        b.update(instance_id='service-B',started=7,finished=7,peak_inflight=3,http_202=2,http_409=5,
                 first_start_elapsed_ns=1_000_000_000,last_finish_elapsed_ns=5_000_000_000)
        return [a,b]
    def test_two_service_instances_counters_audited_without_inventing_global_qps(self):
        report=merge(self.paths,server_metrics=self.multi_snapshots())
        self.assertEqual(report['outcome'],'PASS',report)
        self.assertIsNone(report['global_throughput_qps'])
        self.assertEqual(report['server_side']['scope'],'multiple_service_jvms_no_shared_monotonic_clock')
        self.assertEqual(report['server_side']['instance_count'],2)
        self.assertEqual(report['server_side']['total_finished'],12)
        self.assertIsNone(report['server_side']['server_handler_peak_inflight'])
        self.assertEqual({v['instance_id'] for v in report['server_side']['per_instance']},{'service-A','service-B'})
    def test_omitted_second_service_cannot_pass(self):
        report=merge(self.paths,server_metrics=self.multi_snapshots()[:1])
        self.assertEqual(report['outcome'],'FAIL')
        self.assertTrue(any('counts disagree' in s for s in report['harness_errors']))
    def test_duplicate_service_identity_cannot_pass(self):
        snapshots=self.multi_snapshots()
        snapshots[1]['instance_id']=snapshots[0]['instance_id']
        report=merge(self.paths,server_metrics=snapshots)
        self.assertEqual(report['outcome'],'FAIL')
        self.assertTrue(any('Duplicate' in s for s in report['harness_errors']))
    def test_multi_service_status_disagreement_cannot_pass(self):
        snapshots=self.multi_snapshots()
        snapshots[1]['http_202']=3
        snapshots[1]['http_409']=4
        self.assertEqual(merge(self.paths,server_metrics=snapshots)['outcome'],'FAIL')
    def test_invalid_metric_boolean_rejected(self):
        snapshots=self.multi_snapshots()
        snapshots[0]['http_202']=True
        self.assertEqual(merge(self.paths,server_metrics=snapshots)['outcome'],'FAIL')
    def set_kernel_fingerprints(self, a, b):
        for folder, fingerprint in zip(self.paths,(a,b)):
            file=folder/'summary.json'
            data=json.loads(file.read_text())
            data['host_environment']={'schema_version':1,'kernel_fingerprint':fingerprint}
            file.write_text(json.dumps(data))
    def test_same_kernel_does_not_qualify_as_independent(self):
        self.set_kernel_fingerprints('a'*64,'a'*64)
        report=merge(self.paths)
        self.assertEqual(report['host_evidence']['status'],'SAME_KERNEL_OBSERVED')
        self.assertEqual(report['outcome'],'PASS')  # idempotency still passed
        fail=merge(self.paths,require_distinct_kernels=True)
        self.assertEqual(fail['outcome'],'FAIL')
    def test_distinct_kernel_reports_still_not_physical_proof(self):
        self.set_kernel_fingerprints('a'*64,'b'*64)
        report=merge(self.paths,require_distinct_kernels=True)
        self.assertEqual(report['outcome'],'PASS',report)
        self.assertEqual(report['host_evidence']['status'],'DISTINCT_KERNEL_REPORTS_UNVERIFIED')
        self.assertIsNone(report['global_throughput_qps'])
    def test_missing_host_provenance_explicit_gate_fails(self):
        report=merge(self.paths,require_distinct_kernels=True)
        self.assertEqual(report['outcome'],'FAIL')
        self.assertEqual(report['host_evidence']['status'],'UNKNOWN_MISSING_OR_LEGACY_HOST_EVIDENCE')
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
