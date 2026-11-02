"""Distributed launcher preparation; does not require a second host."""
import pathlib,sys,unittest
from types import SimpleNamespace
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
import distributed_prepare as dp

def args(url='https://example.test:443'):
    return dp.parse_args(['--url',url,'--shards','3','--jvms-per-host','2,3,4',
      '--requests','90','--keys','20','--run-id','multi-test','--output-dir','/tmp/not-created'])
class DistributedPrepareTests(unittest.TestCase):
    def test_all_hosts_share_global_schedule_and_start_time(self):
        scripts,manifest=dp.build_scripts(args(),now_ms=1234567890000)
        self.assertEqual(len(scripts),3)
        self.assertEqual(manifest['start_at_epoch_ms'],1234567980000)
        for i in range(3):
            script=scripts[f'launch-host-{i:03d}.sh']
            self.assertIn(f'--shard-index {i}',script)
            self.assertIn('--start-at-ms 1234567980000',script)
            self.assertIn('--run-id multi-test',script)
            self.assertNotIn('password',script.lower())
            self.assertIn('--allow-remote',script)
    def test_insecure_remote_http_disallowed(self):
        with self.assertRaisesRegex(ValueError,'would disclose bearer tokens'):
            dp.build_scripts(args('http://external.example:9090'),now_ms=1234)
    def test_loopback_for_tunnel_supported(self):
        scripts,_=dp.build_scripts(args('http://127.0.0.1:18131'),now_ms=1234)
        self.assertFalse(any('--allow-remote' in s for s in scripts.values()))
    def test_bad_host_jvm_counts_rejected(self):
        a=args();a.jvms_per_host='2,2'
        with self.assertRaisesRegex(ValueError,'JVM count per host'):
            dp.build_scripts(a,now_ms=1234)
    def test_clock_barrier_guard(self):
        a=args();a.start_after_seconds=1
        with self.assertRaisesRegex(ValueError,'Start delay'):
            dp.build_scripts(a,now_ms=1234)
if __name__=='__main__':unittest.main()
