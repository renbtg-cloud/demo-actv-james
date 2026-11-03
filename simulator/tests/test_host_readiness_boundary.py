"""Adversarial Linux host preflight: boundaries, output publication and no network leakage.

All probes are local. No experiment here claims separate physical hosts,
secure operator directory custody, external provider effects or trusted clocks.
"""
import copy
import json
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

SIM=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SIM))
from distributed_prepare import parse_args,build_scripts
from host_readiness import check,_dial,_parse_preflight_target,_read_launcher_snapshot


class HostBoundary(unittest.TestCase):
    def setUp(self):
        scratch=tempfile.TemporaryDirectory(prefix='deroyque-host-boundary-')
        self.addCleanup(scratch.cleanup)
        self.root=pathlib.Path(scratch.name)
        self.now=int(time.time()*1000)
        args=parse_args(['--url','http://127.0.0.1:18471','--shards','2',
                         '--jvms-per-host','1','--requests','8','--keys','2',
                         '--run-id','boundary','--start-after-seconds','180',
                         '--output-dir',str(self.root/'unused')])
        scripts,self.manifest=build_scripts(args,now_ms=self.now)
        self.launch=self.root/'launch-host-000.sh'
        self.launch.write_text(scripts[self.launch.name])
        self.manifest_path=self.root/'manifest.json'
        self.manifest_path.write_text(json.dumps(self.manifest))

    def command(self,output):
        return [sys.executable,str(SIM/'host_readiness.py'),'--manifest',
                str(self.manifest_path),'--shard-index','0',
                '--launcher',str(self.launch),'--output',str(output)]

    def test_m147_symlinked_launcher_rejected(self):
        real=self.root/'source.sh'
        self.launch.rename(real)
        self.launch.symlink_to(real)
        with self.assertRaises(OSError):
            _read_launcher_snapshot(self.launch)

    def test_m147_fifo_launcher_rejected_without_blocking(self):
        self.launch.unlink()
        os.mkfifo(self.launch)
        started=time.monotonic()
        with self.assertRaisesRegex(ValueError,'Unsafe'):
            _read_launcher_snapshot(self.launch)
        self.assertLess(time.monotonic()-started,1.5)

    def test_m147_nonempty_regular_and_size_ceiling(self):
        for content in (b'',b'x'*65537):
            with self.subTest(size=len(content)):
                self.launch.write_bytes(content)
                with self.assertRaisesRegex(ValueError,'Unsafe'):
                    _read_launcher_snapshot(self.launch)

    def test_m148_concurrent_metadata_change_rejected(self):
        import host_readiness
        original=host_readiness.os.fstat
        called=0
        def changed(fd):
            nonlocal called
            called+=1
            stat_result=original(fd)
            if called==2:
                values={field:getattr(stat_result,field) for field in
                        ('st_dev','st_ino','st_size','st_mtime_ns','st_ctime_ns','st_mode')}
                values['st_mtime_ns']+=1
                return SimpleNamespace(**values)
            return stat_result
        with patch('host_readiness.os.fstat',side_effect=changed):
            with self.assertRaisesRegex(ValueError,'changed during snapshot'):
                _read_launcher_snapshot(self.launch)

    def test_m148_replay_of_changed_launcher_fails_expected_digest(self):
        self.launch.write_bytes(self.launch.read_bytes()+b'\n# payload change\n')
        with self.assertRaisesRegex(ValueError,'differs from collector plan'):
            check(self.manifest,0,self.launch,now_ms=self.now)

    def test_m149_private_output_even_under_umask_zero(self):
        dest=self.root/'result.json'
        proc=subprocess.run(self.command(dest),capture_output=True,text=True,
                            timeout=12,preexec_fn=lambda:os.umask(0))
        self.assertEqual(proc.returncode,0,proc.stderr)
        self.assertEqual(dest.stat().st_mode & 0o777,0o600)
        self.assertEqual(json.loads(dest.read_text())['schema_version'],2)
        self.assertFalse(list(self.root.glob('.*.tmp')))

    def test_m150_symlink_target_never_overwritten(self):
        victim=self.root/'victim';victim.write_text('PROTECTED')
        dest=self.root/'result.json';dest.symlink_to(victim)
        proc=subprocess.run(self.command(dest),capture_output=True,text=True,timeout=12)
        self.assertEqual(proc.returncode,2,proc.stderr)
        self.assertEqual(victim.read_text(),'PROTECTED')
        self.assertTrue(dest.is_symlink())

    def test_m150_concurrent_cli_attempts_single_winner(self):
        dest=self.root/'result.json'
        commands=[subprocess.Popen(self.command(dest),stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE,text=True) for _ in range(6)]
        output=[p.communicate(timeout=15) for p in commands]
        self.assertEqual(sum(p.returncode==0 for p in commands),1,output)
        self.assertEqual(sum(p.returncode==2 for p in commands),5,output)
        self.assertEqual(json.loads(dest.read_text())['schema_version'],2)
        self.assertEqual(dest.stat().st_mode & 0o777,0o600)
        self.assertEqual(list(self.root.glob('.*.tmp')),[])

    def test_m151_malformed_target_rejected_without_connecting(self):
        for target in ('http://localhost:bad', 'http://localhost:65536',
                       'http://localhost:0','http://admin:pass@localhost',
                       'https://example.org/api','https://example.org?q=x',
                       'http://example.org:80','http://[::1',
                       'ftp://localhost','http://localhost\n'):
            with self.subTest(target=target):
                with self.assertRaises(ValueError):
                    _parse_preflight_target(target)

    def test_m152_poisoned_localhost_dns_rejected_without_connection(self):
        fake=[(socket.AF_INET,socket.SOCK_STREAM,0,'',('127.0.0.1',80)),
              (socket.AF_INET,socket.SOCK_STREAM,0,'',('198.51.100.12',80))]
        with patch('host_readiness.socket.getaddrinfo',return_value=fake), \
             patch('host_readiness.socket.create_connection') as conn:
            with self.assertRaisesRegex(ValueError,'outside loopback'):
                _dial('http://localhost:80',0.5)
            conn.assert_not_called()

    def test_m152_loopback_connects_to_pinned_numeric_ip(self):
        fake=[(socket.AF_INET,socket.SOCK_STREAM,0,'',('127.0.0.1',9876))]
        with patch('host_readiness.socket.getaddrinfo',return_value=fake), \
             patch('host_readiness.socket.create_connection') as conn:
            self.assertEqual(_dial('http://localhost:9876',0.5),
                             'LOOPBACK_TCP_CONNECTED_NO_APPLICATION_AUTH')
            conn.assert_called_once_with(('127.0.0.1',9876),timeout=0.5)

    def test_m153_invalid_target_fails_without_network_probe(self):
        bad=copy.deepcopy(self.manifest)
        bad['target_url']='https://example.org:INVALID'
        with patch('host_readiness._dial') as dial:
            with self.assertRaisesRegex(ValueError,'Malformed'):
                check(bad,0,self.launch,now_ms=self.now,check_network=True)
            dial.assert_not_called()

    def test_m154_default_preflight_never_dials_network(self):
        with patch('host_readiness._dial') as dial:
            valid=check(self.manifest,0,self.launch,now_ms=self.now)
            self.assertEqual(valid['network_evidence'],'NOT_PROBED')
            dial.assert_not_called()

    def test_m154_plaintext_remote_rejected_even_without_probe(self):
        changed=copy.deepcopy(self.manifest)
        changed['target_url']='http://example.org:80'
        with patch('host_readiness._dial') as dial:
            with self.assertRaisesRegex(ValueError,'plaintext'):
                check(changed,0,self.launch,now_ms=self.now,check_network=False)
            dial.assert_not_called()


if __name__=='__main__':unittest.main()
