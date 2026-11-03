"""Real Linux Java and generated-launch preflight with controlled adversarial inputs."""
import json
import pathlib
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
SIM=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SIM))
from distributed_prepare import parse_args,build_scripts
from host_readiness import check, _dial

class Readiness(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(prefix='deroyque-readiness-')
        self.addCleanup(tmp.cleanup)
        self.root=pathlib.Path(tmp.name)
        argv=parse_args(['--url','http://127.0.0.1:19081','--shards','2',
                        '--jvms-per-host','2','--requests','16','--keys','4',
                        '--run-id','ready-test','--output-dir',str(self.root/'plan')])
        self.now=int(time.time()*1000)
        scripts,self.manifest=build_scripts(argv,now_ms=self.now)
        self.launch=[]
        for name,source in scripts.items():
            p=self.root/name;p.write_text(source);p.chmod(0o750);self.launch.append(p)

    def test_real_java_and_python_environment_is_observed(self):
        report=check(self.manifest,0,self.launch[0],now_ms=self.now)
        self.assertGreaterEqual(report['java_major'],21)
        self.assertEqual(report['network_evidence'],'NOT_PROBED')
        self.assertEqual(report['assigned_java_processes'],2)
        self.assertEqual(len(report['kernel_fingerprint']),64)

    def test_generated_two_host_launchers_have_distinct_shards(self):
        first=check(self.manifest,0,self.launch[0],now_ms=self.now)
        second=check(self.manifest,1,self.launch[1],now_ms=self.now)
        self.assertNotEqual(first['launch_script_sha256'],second['launch_script_sha256'])
        # Same Linux kernel: proof of local rehearsal only, not separate hosts.
        self.assertEqual(first['kernel_fingerprint'],second['kernel_fingerprint'])

    def test_reject_tampered_script(self):
        self.launch[0].write_text(self.launch[0].read_text()+'\n# modified\n')
        with self.assertRaisesRegex(ValueError,'differs'):
            check(self.manifest,0,self.launch[0],now_ms=self.now)

    def test_reject_wrong_shard(self):
        with self.assertRaisesRegex(ValueError,'assigned launcher'):
            check(self.manifest,1,self.launch[0],now_ms=self.now)

    def test_reject_past_barrier(self):
        with self.assertRaisesRegex(ValueError,'barrier'):
            check(self.manifest,0,self.launch[0],now_ms=self.now+100000)

    def test_reject_oversized_jvm_heap_for_cgroup(self):
        from host_provenance import snapshot
        base=snapshot(self.manifest['run_id'])
        base['cgroup_memory_limit']='10000'
        with patch('host_readiness.snapshot',return_value=base):
            with self.assertRaisesRegex(ValueError,'heaps'):
                check(self.manifest,0,self.launch[0],now_ms=self.now)

    def test_reject_huge_free_disk_requirement(self):
        from types import SimpleNamespace
        with patch('host_readiness.os.statvfs',return_value=SimpleNamespace(f_bavail=1,f_frsize=4096)):
            with self.assertRaisesRegex(ValueError,'disk'):
                check(self.manifest,0,self.launch[0],now_ms=self.now,min_free_mib=128)

    def test_reject_missing_or_wrong_runtime(self):
        with self.assertRaises(FileNotFoundError):
            check(self.manifest,0,self.launch[0],now_ms=self.now,java_binary='__missing_deroyque_java__')

    def test_network_dial_does_not_send_application_request(self):
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(1)
        self.addCleanup(listener.close)
        host,port=listener.getsockname()
        self.assertEqual(_dial(f'http://127.0.0.1:{port}',.5),
                         'LOOPBACK_TCP_CONNECTED_NO_APPLICATION_AUTH')
        client,addr=listener.accept();client.settimeout(.1)
        with client:
            self.assertEqual(client.recv(1),b'')

    def test_reject_plaintext_external_target(self):
        with self.assertRaisesRegex(ValueError,'plaintext'):
            _dial('http://example.com:80',.1)

    def test_cli_real_child_process_and_no_overwrite(self):
        (self.root/'manifest.json').write_text(json.dumps(self.manifest))
        args=[sys.executable,str(SIM/'host_readiness.py'),'--manifest',str(self.root/'manifest.json'),
              '--shard-index','0','--launcher',str(self.launch[0]),'--output',str(self.root/'result.json')]
        a=subprocess.run(args,capture_output=True,text=True,timeout=12)
        self.assertEqual(a.returncode,0,a.stderr)
        self.assertEqual((self.root/'result.json').stat().st_mode & 0o777,0o600)
        b=subprocess.run(args,capture_output=True,text=True,timeout=12)
        self.assertNotEqual(b.returncode,0)
        self.assertIn('File exists',b.stderr)

if __name__=='__main__':unittest.main()
