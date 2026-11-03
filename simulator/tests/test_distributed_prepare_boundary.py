"""Local generator invariants: invalid plans never become publishable host launchers."""
import copy
import hashlib
import json
import os
import pathlib
import socket
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SIM=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SIM))
from distributed_prepare import build_scripts,parse_args,_publish_plan


class PrepareBoundary(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix='deroyque-prepare-boundary-')
        self.addCleanup(temp.cleanup)
        self.root=pathlib.Path(temp.name)

    def args(self,*extra):
        return parse_args(['--url','http://127.0.0.1:18881','--shards','2',
                           '--jvms-per-host','2','--requests','16','--keys','4',
                           '--run-id','preparation-test','--start-after-seconds','180',
                           '--output-dir',str(self.root/'published'),*extra])

    def gen(self,*extra):
        return build_scripts(self.args(*extra),now_ms=1600000000000)

    def command(self,path):
        return [sys.executable,str(SIM/'distributed_prepare.py'), '--url',
                'http://127.0.0.1:18881','--shards','2','--jvms-per-host','2',
                '--requests','16','--keys','4','--run-id','preparation-test',
                '--start-after-seconds','180','--output-dir',str(path)]

    def test_m155_reject_nonfinite_workload_numbers(self):
        for option,value in [('--rate','nan'),('--rate','inf'),('--duration-seconds','nan'),
                             ('--skew-power','nan'),('--burst-rate','nan'),
                             ('--ramp-start-rate','inf'),('--rate=-inf',None)]:
            with self.subTest(option=option,value=value):
                with self.assertRaises(ValueError):
                    self.gen(option,*([value] if value is not None else []))

    def test_m155_reject_profile_without_required_duration(self):
        with self.assertRaisesRegex(ValueError,'ramp-up requires'):
            self.gen('--profile','ramp-up','--duration-seconds','0')
        with self.assertRaisesRegex(ValueError,'soak requires'):
            self.gen('--profile','soak','--duration-seconds','0')

    def test_m156_reject_conflicting_unique_key_distribution(self):
        with self.assertRaisesRegex(ValueError,'unique distribution'):
            self.gen('--distribution','unique')

    def test_m156_reject_invalid_burst_and_hotspot_parameters(self):
        with self.assertRaisesRegex(ValueError,'burst requires'):
            self.gen('--profile','burst','--rate','50','--burst-rate','30')
        with self.assertRaisesRegex(ValueError,'hotspot-percent'):
            self.gen('--hotspot-percent','0')
        with self.assertRaisesRegex(ValueError,'skew-power'):
            self.gen('--skew-power','-1')

    def test_m157_generator_and_preflight_share_remote_url_contract(self):
        for url in ('http://example.com:80','https://example.com:bad',
                    'https://example.com/api','http://localhost:0',
                    'https://user:password@example.com'):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    build_scripts(self.args('--url',url),now_ms=1600000000000)

    def test_m157_loopback_alias_and_other_127_subnet_are_local(self):
        for url in ('http://localhost:18881','http://127.0.0.2:18881','http://[::1]:18881'):
            with self.subTest(url=url):
                scripts,manifest=build_scripts(self.args('--url',url),now_ms=1600000000000)
                self.assertNotIn('--allow-remote',scripts['launch-host-000.sh'])
                self.assertEqual(manifest['target_url'],url)

    def test_m158_reject_unbounded_resources_and_bad_time(self):
        for flags in [('--heap-max-mb','1'),('--heap-max-mb','65537'),
                      ('--concurrency','0'),('--concurrency','10001'),
                      ('--start-after-seconds','86401'),('--start-after-seconds','1')]:
            with self.subTest(flags=flags):
                with self.assertRaises(ValueError):self.gen(*flags)
        with self.assertRaisesRegex(ValueError,'reference UTC'):
            build_scripts(self.args(),now_ms=True)

    def test_m159_real_cli_private_complete_published_plan(self):
        dest=self.root/'published'
        run=subprocess.run(self.command(dest),capture_output=True,text=True,
                           preexec_fn=lambda:os.umask(0),timeout=15)
        self.assertEqual(run.returncode,0,run.stderr)
        self.assertEqual(stat.S_IMODE(dest.stat().st_mode),0o700)
        manifest=json.loads((dest/'manifest.json').read_text())
        self.assertEqual(stat.S_IMODE((dest/'manifest.json').stat().st_mode),0o600)
        self.assertEqual(set(p.name for p in dest.iterdir()),
                         {'manifest.json','launch-host-000.sh','launch-host-001.sh'})
        for i in range(2):
            path=dest/f'launch-host-{i:03d}.sh'
            self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o700)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),
                             manifest['launch_sha256'][path.name])
        self.assertEqual(list(self.root.glob('.deroyque-plan-*')),[])

    def test_m160_existing_directory_never_overwritten(self):
        dest=self.root/'published';dest.mkdir();(dest/'sentinel').write_text('PROTECTED')
        cmd=self.command(dest)
        run=subprocess.run(cmd,capture_output=True,text=True,timeout=12)
        self.assertEqual(run.returncode,2)
        self.assertEqual((dest/'sentinel').read_text(),'PROTECTED')
        self.assertEqual(list(self.root.glob('.deroyque-plan-*')),[])

    def test_m160_existing_symlink_never_followed(self):
        source=self.root/'real';source.mkdir();(source/'sentinel').write_text('PROTECTED')
        dest=self.root/'published';dest.symlink_to(source)
        run=subprocess.run(self.command(dest),capture_output=True,text=True,timeout=12)
        self.assertEqual(run.returncode,2)
        self.assertEqual((source/'sentinel').read_text(),'PROTECTED')
        self.assertEqual(list(self.root.glob('.deroyque-plan-*')),[])

    def test_m161_six_independent_cli_racers_one_published_plan(self):
        dest=self.root/'published'
        procs=[subprocess.Popen(self.command(dest),stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,text=True) for _ in range(6)]
        output=[p.communicate(timeout=20) for p in procs]
        self.assertEqual(sum(p.returncode==0 for p in procs),1,output)
        self.assertEqual(sum(p.returncode==2 for p in procs),5,output)
        self.assertEqual(len(list(dest.iterdir())),3)
        self.assertEqual(list(self.root.glob('.deroyque-plan-*')),[])

    def test_m162_serialization_error_leaves_no_publication(self):
        scripts,manifest=self.gen()
        changed=copy.deepcopy(manifest);changed['launch_plan']['target_qps']=float('nan')
        with self.assertRaises(ValueError):
            _publish_plan(self.root/'published',scripts,changed)
        self.assertFalse((self.root/'published').exists())
        self.assertEqual(list(self.root.glob('.deroyque-plan-*')),[])

    def test_m162_invalid_script_name_never_published(self):
        scripts,manifest=self.gen()
        scripts['../unsafe.sh']='echo 1'
        with self.assertRaisesRegex(ValueError,'launcher path'):
            _publish_plan(self.root/'published',scripts,manifest)
        self.assertFalse((self.root/'published').exists())
        self.assertEqual(list(self.root.glob('.deroyque-plan-*')),[])


if __name__=='__main__':unittest.main()
