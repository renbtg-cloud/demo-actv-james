import os,pathlib,sys,unittest,tempfile
from unittest import mock
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from cpu_placement import parse_cpus,format_cpus,pid_cpus,proc_cpu_threads
import run
import saturation
import thread_diagnostics

class CPUPlacementTests(unittest.TestCase):
    def test_parser(self):
        self.assertEqual(parse_cpus('0,2-3',available={0,1,2,3}),{0,2,3})
        self.assertEqual(format_cpus({3,0,2}),'0,2,3')
        for bad in ('','1-0','3-50000','1,,2','-2','x', '1-2-3'):
            with self.subTest(bad=bad),self.assertRaises(ValueError):parse_cpus(bad,available=set(range(10)))
    def test_disallowed(self):
        with self.assertRaises(ValueError):parse_cpus('0,2',available={0,1})
    def test_own_pid_masks(self):
        self.assertEqual(pid_cpus(os.getpid()),sorted(os.sched_getaffinity(0)))
        v=proc_cpu_threads(os.getpid())
        self.assertGreaterEqual(v['thread_count_checked'],1)
    def test_yaml_affinity(self):
        with tempfile.TemporaryDirectory() as d:
            conf=pathlib.Path(d)/'work.yaml'
            cpu=min(os.sched_getaffinity(0));conf.write_text(f'producer-cpus: "{cpu}"\n')
            a=run.parse_args(['--config',str(conf)])
            self.assertEqual(a.producer_cpus,str(cpu))
    def test_saturation_disjoint_core_validation(self):
        cpus=sorted(os.sched_getaffinity(0))
        if len(cpus)<2:self.skipTest('requires >=2 allowed CPUs')
        a,b=cpus[:2]
        with tempfile.TemporaryDirectory() as d:
            # fake /proc service PID validation is isolated by mocking PID mask and argv
            with mock.patch('saturation.pid_cpus',return_value=[b]),mock.patch('saturation.proc_cpu_threads',return_value={'leader_cpus':[b],'thread_count_checked':1,'threads_with_other_affinity':0}),mock.patch('pathlib.Path.read_bytes',return_value=b'java demo.DeroyqueApplication'):
                good=saturation.parse(['--tier','1:5:5','--output-dir',str(pathlib.Path(d)/'new'), '--threads','off','--service-pid',str(os.getpid()),'--expected-service-cpus',str(b),'--producer-cpus',str(a)])
                self.assertEqual(good.producer_cpus,str(a))
                with self.assertRaises(SystemExit):saturation.parse(['--tier','1:5:5','--output-dir',str(pathlib.Path(d)/'new2'),'--threads','off','--service-pid',str(os.getpid()),'--expected-service-cpus',str(b),'--producer-cpus',str(b)])
    def test_jfr_pinning_severity_counts(self):
        import json
        record={'recording':{'events':[
          {'type':'jdk.VirtualThreadPinned','values':{'duration':'PT0.0003S','stackTrace':{'frames':[]}}},
          {'type':'jdk.VirtualThreadPinned','values':{'duration':'PT0.12S','stackTrace':{'frames':[]}}},
          {'type':'jdk.VirtualThreadPinned','values':{'duration':'PT0.018S','stackTrace':{'frames':[]}}}]}}
        with tempfile.TemporaryDirectory() as d:
            f=pathlib.Path(d)/'unused.jfr';f.touch()
            with mock.patch.object(thread_diagnostics.subprocess,'run',return_value=mock.Mock(returncode=0,stdout=json.dumps(record),stderr='')):
                v=thread_diagnostics.jfr_events(f)
            self.assertEqual(v['virtual_thread_pinned_duration_buckets'],{'lt_1ms':1,'1_to_lt_10ms':0,'10_to_lt_100ms':1,'100ms_and_more':1})
            self.assertEqual(v['virtual_thread_pinned_stack_samples'][0]['duration_ms'],120)
    def test_jfr_diagnostic_configuration(self):
        with tempfile.TemporaryDirectory() as d:
            res=thread_diagnostics.jfr_configure_focus(pathlib.Path(d)/'focus.jfc')
            self.assertTrue(res['ok'],res)
            data=(pathlib.Path(d)/'focus.jfc').read_text()
            self.assertIn('jdk.VirtualThreadPinned',data)
            self.assertIn('0 ms',data)

if __name__=='__main__':unittest.main()
