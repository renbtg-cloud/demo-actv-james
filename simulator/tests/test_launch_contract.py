"""Canonical launch-contract adversarial regression: no remote host is contacted."""
import copy
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

SIM=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SIM))
from distributed_prepare import build_scripts,parse_args
from distributed_integrity import verify_launch_scripts,verify_launch
from host_readiness import check
from launch_contract import replay_manifest


class CanonicalLaunchContract(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(prefix='deroyque-launch-contract-')
        self.addCleanup(tmp.cleanup)
        self.root=pathlib.Path(tmp.name)
        self.now=int(time.time()*1000)
        self.args=['--url','http://127.0.0.1:18977','--shards','2',
                   '--jvms-per-host','1','--requests','12','--keys','4',
                   '--run-id','canonical-test','--start-after-seconds','180',
                   '--output-dir',str(self.root/'unused')]
        self.prepare()

    def prepare(self,*args,base=None):
        argv=parse_args(self.args+list(args))
        self.scripts,self.manifest=build_scripts(argv,now_ms=self.now)
        self.folder=self.root/'plan';self.folder.mkdir(exist_ok=True)
        for f in self.folder.glob('launch-host-*.sh'):f.unlink()
        (self.folder/'manifest.json').write_text(json.dumps(self.manifest))
        for name,source in self.scripts.items():
            (self.folder/name).write_text(source)

    def verify_local(self):
        return verify_launch_scripts(self.folder)

    def changed_manifest(self,mutator):
        m=copy.deepcopy(self.manifest)
        mutator(m)
        (self.folder/'manifest.json').write_text(json.dumps(m))
        return m

    def test_m163_generated_manifest_has_exact_canonical_schema(self):
        self.assertEqual(replay_manifest(self.manifest),
                         {n:s.encode('utf-8') for n,s in self.scripts.items()})
        self.assertEqual(self.verify_local(),[])

    def test_m163_extra_and_missing_manifest_keys_fail(self):
        for mutate in (lambda m:m.update(secret='bad'),lambda m:m.pop('target_url')):
            with self.subTest(mutate=mutate):
                m=self.changed_manifest(mutate)
                with self.assertRaisesRegex(ValueError,'schema'):replay_manifest(m)

    def test_m164_forged_script_with_matching_updated_checksum_fails(self):
        # Old integrity check accepted any script when the manifest included
        # its new hash; now the command must match the declared plan.
        p=self.folder/'launch-host-000.sh'
        changed=p.read_text().replace('simulator/run.py','simulator/malicious.py')
        p.write_text(changed)
        m=self.changed_manifest(lambda v:v['launch_sha256'].__setitem__(
            p.name,hashlib.sha256(changed.encode()).hexdigest()))
        self.assertTrue(self.verify_local())
        with self.assertRaisesRegex(ValueError,'canonical'):replay_manifest(m)
        with self.assertRaises(ValueError):check(m,0,p,now_ms=self.now)

    def test_m164_forged_manifest_target_not_obeyed_by_script(self):
        m=self.changed_manifest(lambda v:v.__setitem__('target_url','https://example.com:443'))
        self.assertTrue(self.verify_local())
        with self.assertRaisesRegex(ValueError,'canonical'):replay_manifest(m)

    def test_m165_missing_or_mislabeled_checksum_inventory_fails(self):
        for mutate in (lambda m:m['launch_sha256'].pop('launch-host-001.sh'),
                       lambda m:m['launch_sha256'].update({'launch-host-999.sh':'0'*64}),
                       lambda m:m['launch_sha256'].update({'launch-host-000.sh':'G'*64})):
            with self.subTest(mutate=mutate):
                m=self.changed_manifest(mutate)
                with self.assertRaisesRegex(ValueError,'inventory'):replay_manifest(m)
                self.assertTrue(self.verify_local())

    def test_m165_missing_launcher_and_extra_launcher_fail(self):
        (self.folder/'launch-host-001.sh').unlink()
        self.assertTrue(self.verify_local())
        self.prepare()
        (self.folder/'launch-host-099.sh').write_text('# extra')
        self.assertTrue(self.verify_local())

    def test_m166_preflight_rejects_changed_identity_before_network_and_java(self):
        for key,value in [('run_id','swapped'),('target_url','https://example.com'),
                          ('start_at_epoch_ms',self.manifest['start_at_epoch_ms']+1000)]:
            with self.subTest(key=key):
                m=copy.deepcopy(self.manifest);m[key]=value
                with patch('host_readiness._dial') as dial,patch('host_readiness._java_version') as java:
                    with self.assertRaises(ValueError):
                        check(m,0,self.folder/'launch-host-000.sh',now_ms=self.now,check_network=True)
                    dial.assert_not_called();java.assert_not_called()

    def test_m166_reject_mismatched_jvm_allocation_and_workload_volume(self):
        for mutate in (lambda m:m['jvms_per_host'].__setitem__(1,2),
                       lambda m:m.__setitem__('global_posts',42),
                       lambda m:m['launch_plan'].__setitem__('target_qps',41.0)):
            m=copy.deepcopy(self.manifest);mutate(m)
            with self.subTest(m=m):
                with self.assertRaises(ValueError):replay_manifest(m)

    def test_m167_offline_and_host_preflight_same_rejection_policy(self):
        (self.folder/'launch-host-000.sh').write_text('malicious contents')
        self.assertTrue(self.verify_local())
        with self.assertRaises(ValueError):
            check(self.manifest,0,self.folder/'launch-host-000.sh',now_ms=self.now)

    def test_m167_symlink_and_fifo_never_followed_or_blocked(self):
        path=self.folder/'launch-host-000.sh';path.unlink()
        path.symlink_to(self.folder/'launch-host-001.sh')
        self.assertTrue(self.verify_local())
        path.unlink();os.mkfifo(path)
        before=time.monotonic()
        self.assertTrue(self.verify_local())
        self.assertLess(time.monotonic()-before,2.0)

    def test_m168_legacy_int_defaults_and_explicit_float_supported(self):
        for arg in [(),('--rate','60','--hotspot-percent','80','--hotspot-keys-percent','10'),
                    ('--rate','60.0','--hotspot-percent','80.0','--hotspot-keys-percent','10.0')]:
            with self.subTest(arg=arg):
                self.prepare(*arg)
                self.assertEqual(self.verify_local(),[])
                self.assertEqual(len(replay_manifest(self.manifest)),2)

    def test_m168_ramp_burst_soak_and_skewed_round_trip(self):
        configs=[('--profile','ramp-up','--duration-seconds','10','--ramp-start-rate','1'),
                 ('--profile','burst','--duration-seconds','10','--burst-rate','100',
                  '--burst-width-seconds','1','--burst-period-seconds','5'),
                 ('--profile','soak','--duration-seconds','1','--rate','12'),
                 ('--distribution','skewed','--skew-power','2')]
        for opt in configs:
            with self.subTest(opt=opt):
                self.prepare(*opt)
                self.assertEqual(self.verify_local(),[])

    def test_m169_reject_type_confusion_and_nonfinite_plan(self):
        mutants=[lambda m:m.__setitem__('schema_version',True),
                 lambda m:m.__setitem__('shards',2.0),
                 lambda m:m['jvms_per_host'].__setitem__(0,True),
                 lambda m:m['launch_plan'].__setitem__('rate','too fast'),
                 lambda m:m['launch_plan'].__setitem__('target_qps',float('nan')),
                 lambda m:m['launch_plan'].__setitem__('seed',True),
                 lambda m:m['launch_plan'].update({'unknown':123})]
        for i,mutate in enumerate(mutants):
            m=copy.deepcopy(self.manifest);mutate(m)
            with self.subTest(index=i):
                with self.assertRaises((ValueError,TypeError,KeyError)):
                    replay_manifest(m)

    def test_m169_contradictory_plan_metadata_fails_full_verification(self):
        m=copy.deepcopy(self.manifest);m['launch_plan']['shard_count']=3
        self.assertTrue(self.changed_manifest(lambda a:a['launch_plan'].__setitem__('shard_count',3)))
        self.assertTrue(self.verify_local())
        with self.assertRaises(ValueError):replay_manifest(m)

    def test_m170_cli_rejects_manifest_forgery_without_creating_report(self):
        m=self.changed_manifest(lambda v:v['launch_plan'].__setitem__('shared_keys',1000))
        dest=self.root/'result.json'
        p=subprocess.run([sys.executable,str(SIM/'host_readiness.py'),
            '--manifest',str(self.folder/'manifest.json'),
            '--launcher',str(self.folder/'launch-host-000.sh'),
            '--shard-index','0','--output',str(dest)],
            capture_output=True,text=True,timeout=15)
        self.assertEqual(p.returncode,2,p.stdout+p.stderr)
        self.assertFalse(dest.exists())


if __name__=='__main__':unittest.main()
