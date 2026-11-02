"""Small deterministic tests for the resource and thread diagnostic interpretation."""
import json
import pathlib
import tempfile
import unittest
import sys
from unittest import mock
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
import saturation as sat
import thread_diagnostics as td

class SaturationTests(unittest.TestCase):
    def test_tiers_and_limits(self):
        self.assertEqual(sat.tier('12:125.5:240'),(12,125.5,240))
        for bad in ('0:100:100','12:-1:240','12:nan:240','8:40:4','999:10:1000'):
            with self.subTest(bad=bad),self.assertRaises(Exception):sat.tier(bad)
    def test_no_override_of_previous_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            pathlib.Path(d,'existing').mkdir()
            with self.assertRaises(SystemExit):sat.parse(['--tier','1:10:20','--threads','off','--output-dir',str(pathlib.Path(d,'existing'))])
    def test_unauthorized_remote_target(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):sat.parse(['--tier','1:10:20','--threads','off','--url','http://example.org','--output-dir',str(pathlib.Path(d,'new'))])
    def test_clipped_total_posts(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(SystemExit):sat.parse(['--tier','2:10:900','--tier','2:10:900','--max-total-posts','1000','--threads','off','--output-dir',str(pathlib.Path(d,'new'))])
    def test_rate_and_lag_are_not_confused(self):
        result={'outcome':'PASS','observed':{'post_attempts_per_second':75,'latency_ms':{'95':9},'schedule_lag_ms':{'95':0.4}}}
        self.assertEqual(sat.describe(result,100,100,20,.8),'BELOW_TARGET_ROOT_CAUSE_UNDETERMINED')
        result['observed']['schedule_lag_ms']['95']=30
        self.assertEqual(sat.describe(result,100,100,20,.8),'GENERATOR_UNABLE_TO_MEET_SCHEDULE_OR_SHARED_CONTENTION')
        result['observed']['schedule_lag_ms']['95']=1
        result['observed']['post_attempts_per_second']=100
        self.assertEqual(sat.describe(result,100,100,20,.8),'TARGET_SUSTAINED_WITHIN_THRESHOLDS')
        result['observed']['latency_ms']['95']=101
        self.assertEqual(sat.describe(result,100,100,20,.8),'LATENCY_LIMIT_EXCEEDED_ROOT_CAUSE_UNDETERMINED')
        result['outcome']='FAIL'
        self.assertEqual(sat.describe(result,100,100,20,.8),'FAIL_INVARIANT_OR_TRANSPORT')
    def test_cpu_is_preserved_across_exited_jvms(self):
        prev={};tot=0.0
        tot=sat.integrate_producer_cpu([{'pid':101,'cpu_s':.4}],prev,tot)
        tot=sat.integrate_producer_cpu([{'pid':101,'cpu_s':.9},{'pid':102,'cpu_s':.2}],prev,tot)
        tot=sat.integrate_producer_cpu([{'pid':102,'cpu_s':.5}],prev,tot)
        tot=sat.integrate_producer_cpu([],prev,tot)
        self.assertAlmostEqual(tot,1.4)
    def test_cpu_cores_delta(self):
        sample=[{'epoch_ms':1000,'service_cpu_seconds':5},{'epoch_ms':3000,'service_cpu_seconds':7.3}]
        self.assertEqual(sat.cpu_load(sample,'service_cpu_seconds'),1.15)
        self.assertIsNone(sat.cpu_load(sample[:1],'service_cpu_seconds'))
        self.assertIsNone(sat.cpu_load([sample[-1],sample[0]],'service_cpu_seconds'))
    def test_saturation_comparison_marks_missing_unrun_data(self):
        from saturation_compare import combine,output
        with tempfile.TemporaryDirectory() as d:
            r=pathlib.Path(d)/'a'/'saturation.json';r.parent.mkdir()
            r.write_text(json.dumps({'tiers':[{'tier':1,'planned_jvms':15,
              'verdict':'RESOURCE_GUARD_REJECTED_STAGE_NOT_RUN'}]}))
            rows=combine([r]);output(rows,pathlib.Path(d)/'comparison')
            doc=(pathlib.Path(d)/'comparison.md').read_text()
            self.assertIn('n/a',doc)
            self.assertIn('RESOURCE_GUARD_REJECTED_STAGE_NOT_RUN',doc)
    def test_jvm_dump_platform_parse(self):
        t='java.lang.Thread.State: BLOCKED\njava.lang.Thread.State: WAITING\nFound one Java-level deadlock:'
        p=td.parse_platform_dump(t)
        self.assertEqual(p['platform_threads_with_state'],2)
        self.assertTrue(p['deadlock_reported'])
    def test_json_dump_not_virtual_thread_state(self):
        p=td.parse_json_dump({'threadDump':{'threadContainers':[{'container':'c','threads':[{'stack':['java.util.concurrent.locks.LockSupport.park()','foo.Baz']},{'stack':['foo.Other']}]}]}})
        self.assertEqual(p['all_listed_threads'],2)
        self.assertEqual(p['sampled_park_stacks'],1)
        self.assertNotIn('virtual_thread_states',p)
    def test_proc_info_of_own_python(self):
        import os
        info=td.proc_info(os.getpid())
        self.assertIsNotNone(info)
        self.assertGreater(info['rss_bytes'],0)
        self.assertGreaterEqual(info['os_threads'],1)
    def test_jfr_zero_not_safety_proof(self):
        with tempfile.TemporaryDirectory() as d:
            path=pathlib.Path(d)/'empty.jfr';path.touch()
            document={'recording':{'events':[]}}
            with mock.patch.object(td.subprocess,'run',return_value=mock.Mock(returncode=0,stdout=json.dumps(document),stderr='')):
                x=td.jfr_events(path)
            self.assertEqual(x['counts'],{})
            self.assertIn('not rule out',x['note'])

if __name__=='__main__':unittest.main()
