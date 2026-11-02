import argparse
import json
import pathlib
import tempfile
import unittest
from unittest import mock
import sys
ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from run import parse_args,validate_target,resource_report,choose_jvms
from workload import create_plan,schedule_seconds
from compare import run as compare

class WorkloadTests(unittest.TestCase):
    def args(self,**settings):
        x=parse_args([])
        return argparse.Namespace(**{**vars(x),**settings})
    def test_yaml_override_and_strict_unknown(self):
        with tempfile.TemporaryDirectory() as folder:
            p=pathlib.Path(folder)/'profile.yaml'
            p.write_text('jvms: 8\nrequests: 200\nprofile: steady\nrate: 50\nverify_state: true\n')
            a=parse_args(['--config',str(p),'--jvms','12'])
            self.assertEqual((a.jvms,a.requests,a.rate,a.verify_state),(12,200,50,True))
            p.write_text('jvms: 8\nunknown_setting: 3\n')
            with self.assertRaisesRegex(ValueError,'Unknown YAML setting'):parse_args(['--config',str(p)])
            p.write_text('verify_state: string-not-boolean\n')
            with self.assertRaisesRegex(ValueError,'boolean'):parse_args(['--config',str(p)])
    def test_steady_is_open_loop_global(self):
        a=self.args(requests=5,keys=5,rate=2)
        self.assertEqual([x[-1] for x in create_plan(a)],[0,500,1000,1500,2000])
    def test_ramp_up_rate_monotonically_increases(self):
        a=self.args(profile='ramp-up',duration_seconds=4,rate=30,ramp_start_rate=2,requests=50,keys=10)
        t=schedule_seconds(a)
        self.assertGreater(t[5]-t[4],t[45]-t[44]);self.assertEqual(t[0],0)
    def test_burst_repeats_and_has_gaps(self):
        a=self.args(profile='burst',rate=2,burst_rate=20,burst_width_seconds=0.5,burst_period_seconds=2,requests=40,keys=10)
        t=schedule_seconds(a)
        self.assertTrue(any(t[i+1]-t[i]>.15 for i in range(len(t)-1)))
        self.assertTrue(any(t[i+1]-t[i]<.07 for i in range(len(t)-1)))
    def test_soak_spreads_across_entire_duration(self):
        a=self.args(profile='soak',duration_seconds=5,requests=11,keys=5)
        self.assertEqual(schedule_seconds(a)[-1],5)
    def test_hotspot_determinism_and_key_coverage(self):
        a=self.args(distribution='hotspot',requests=300,keys=30,conflict_percent=30,seed=23)
        p=list(create_plan(a));self.assertEqual(p,list(create_plan(a)))
        self.assertEqual({x[1] for x in p[:30]},set(range(30)))
        self.assertTrue(all(x[2]==0 for x in p[:30]))
        self.assertGreater(sum(slot<3 for _,slot,_,_ in p[30:]),150)
    def test_unique_rejects_key_collision_config(self):
        with self.assertRaisesRegex(ValueError,'unique distribution'):
            validate_target(self.args(distribution='unique',requests=10,keys=5))
    def test_resource_guard_and_explicit_override(self):
        a=self.args(jvms=256,requests=500,heap_max_mb=100)
        with mock.patch('run.pathlib.Path.read_text',autospec=True,side_effect=lambda *x,**k:'1024' if 'memory.max' in str(x[0]) else '0'):
            with self.assertRaisesRegex(ValueError,'Estimated JVM budget'):resource_report(a)
            a.unsafe_resource_override=True
            self.assertTrue(resource_report(a)['overridden'])
    def test_auto_jvms_respects_budget_and_caps(self):
        a=self.args(auto_jvms=True,auto_max_jvms=64,requests=1000,heap_max_mb=64)
        with mock.patch('run.detect_cgroup_memory',return_value=(4*1024**3,2*1024**3)):
            self.assertEqual(choose_jvms(a),13)
        with mock.patch('run.detect_cgroup_memory',return_value=(4*1024**3,4*1024**3-100)):
            with self.assertRaisesRegex(ValueError,'Insufficient'):choose_jvms(a)
    def test_comparison_writes_two_readable_formats(self):
        data={'outcome':'PASS','plan':{'jvms':4,'profile':'soak'},'observed':{
          'records':40,'unexpected_http_or_transport':0,'peak_jvms_with_inflight_http':4,
          'peak_inflight_http':12,'post_attempts_per_second':40,
          'latency_ms':{'95':25,'99':35},'schedule_lag_ms':{'95':1.4}},
          'violations':[],'harness_errors':[],'state_verification_errors':[]}
        with tempfile.TemporaryDirectory() as d:
            d=pathlib.Path(d);a=d/'a';b=d/'b';a.mkdir();b.mkdir()
            for q in (a,b):(q/'summary.json').write_text(json.dumps(data))
            self.assertEqual(compare([str(a/'summary.json'),str(b/'summary.json'),'--output-prefix',str(d/'compare')]),0)
            self.assertIn('40', (d/'compare.csv').read_text())
            self.assertIn('admission only',(d/'compare.md').read_text())
if __name__=='__main__':unittest.main()
