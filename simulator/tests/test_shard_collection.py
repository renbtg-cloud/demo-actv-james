"""Real offline Ed25519 collector acceptance with safe Linux no-replace publish."""
import copy
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

SIM=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SIM))
from distributed_prepare import parse_args,build_scripts
from host_session import create_session
from host_signed_evidence import make_envelope
from shard_bundle import bundle
from shard_collection import collect

class ShardCollectionTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix='real-collector-')
        self.addCleanup(temp.cleanup)
        self.root=pathlib.Path(temp.name)
        parsed=parse_args(['--url','http://127.0.0.1:18081','--shards','2',
                           '--jvms-per-host','2','--requests','12','--keys','4',
                           '--run-id','signed-ci-run','--output-dir',str(self.root/'unused')])
        _,self.manifest=build_scripts(parsed,now_ms=1793608990000)
        self.pins={};self.privates=[];self.folders=[]
        for i in range(2):
            key=Ed25519PrivateKey.generate()
            private=self.root/f'host-private-{i}.pem'
            private.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
            private.chmod(0o600)
            self.privates.append(private)
            self.pins[f'host-{i}']=key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode()
            folder=self.root/f'producer-{i}';folder.mkdir()
            summary={'run_id':'signed-ci-run','target':self.manifest['target_url'],
                     'plan':{**self.manifest['launch_plan'],'shard_index':i,'jvms':2}}
            (folder/'summary.json').write_text(json.dumps(summary))
            for j in range(2):
                (folder/f'agent-{j:03d}.tsv').write_text(f'host{i}-jvm{j}\n')
            self.folders.append(folder)
        self.session=create_session(self.manifest,self.pins,['host-0','host-1'])
        self.archives=[]
        for i in range(2):
            signed=self.root/f'signed-{i}.json'
            signed.write_text(json.dumps(make_envelope(self.folders[i],'signed-ci-run',f'host-{i}',i,
                          self.privates[i],self.session,self.manifest,self.pins)))
            archive=self.root/f'archive-{i}.zip'
            bundle(self.folders[i],signed,archive)
            self.archives.append(archive)

    def accept(self,output=None,archives=None,session=None,manifest=None,pins=None):
        return collect(archives if archives is not None else self.archives,
               output if output is not None else self.root/'collected',
               pins if pins is not None else self.pins,
               session if session is not None else self.session,
               manifest if manifest is not None else self.manifest)

    def test_valid_inverted_arrival_order_is_published_with_signed_identity(self):
        report=self.accept(archives=self.archives[::-1])
        self.assertEqual(report['signed_host_evidence']['status'],'ALL_PINNED_SIGNING_KEYS_VERIFIED_NOT_HOST_ATTESTATION')
        self.assertEqual([e['shard'] for e in report['bundles']],[0,1])
        self.assertEqual(json.loads((self.root/'collected'/'collection.json').read_text()),report)
        self.assertTrue((self.root/'collected'/'shard-000'/'signed-envelope.json').is_file())

    def test_refuse_preexisting_directory_without_modification(self):
        folder=self.root/'collected';folder.mkdir();(folder/'sentinel').write_text('old')
        with self.assertRaisesRegex(ValueError,'Destination'):
            self.accept()
        self.assertEqual((folder/'sentinel').read_text(),'old')

    def test_refuse_duplicate_or_missing_archive_before_extraction(self):
        with self.assertRaisesRegex(ValueError,'duplicate'):
            self.accept(archives=self.archives[:1]*2)
        with self.assertRaisesRegex(ValueError,'exactly one'):
            self.accept(archives=self.archives[:1])
        self.assertFalse((self.root/'collected').exists())

    def test_refuse_replay_with_different_session_nonce(self):
        changed=create_session(self.manifest,self.pins,['host-0','host-1'])
        with self.assertRaisesRegex(ValueError,'Ed25519 verification'):
            self.accept(session=changed)
        self.assertFalse((self.root/'collected').exists())

    def test_refuse_wrong_host_assignment(self):
        revised=create_session(self.manifest,self.pins,['host-1','host-0'])
        with self.assertRaisesRegex(ValueError,'Ed25519 verification'):
            self.accept(session=revised)
        self.assertFalse((self.root/'collected').exists())

    def test_refuse_manifest_mutation(self):
        altered=copy.deepcopy(self.manifest)
        altered['shared_keys']=9
        with self.assertRaisesRegex(ValueError,'Session differs'):
            self.accept(manifest=altered)
        self.assertFalse((self.root/'collected').exists())

    def test_refuse_summary_changed_with_manifest_updated_but_old_signature(self):
        p=self.folders[0]/'summary.json'
        obj=json.loads(p.read_text());obj['plan']['seed']=31337
        p.write_text(json.dumps(obj))
        archive=self.root/'forged-summary.zip'
        signed=self.root/'signed-0.json'
        bundle(self.folders[0],signed,archive)
        with self.assertRaisesRegex(ValueError,'Shards disagree'):
            self.accept(archives=[archive,self.archives[1]])
        self.assertFalse((self.root/'collected').exists())

    def test_refuse_signed_member_tamper_after_resealing_archive(self):
        p=self.folders[1]/'agent-000.tsv';p.write_text('malicious new payload\n')
        archive=self.root/'resealed.zip'
        bundle(self.folders[1],self.root/'signed-1.json',archive)
        with self.assertRaisesRegex(ValueError,'Ed25519 verification'):
            self.accept(archives=[self.archives[0],archive])
        self.assertFalse((self.root/'collected').exists())

    def signed_provenance_archives(self, fingerprints):
        out=[]
        for i,fingerprint in enumerate(fingerprints):
            summary=self.folders[i]/'summary.json'
            document=json.loads(summary.read_text())
            document['host_environment']={'schema_version':1,'kernel_fingerprint':fingerprint}
            summary.write_text(json.dumps(document))
            signed=self.root/f'provenance-signed-{i}.json'
            signed.write_text(json.dumps(make_envelope(self.folders[i],'signed-ci-run',
                f'host-{i}',i,self.privates[i],self.session,self.manifest,self.pins)))
            archive=self.root/f'provenance-{i}.zip'
            bundle(self.folders[i],signed,archive)
            out.append(archive)
        return out

    def test_same_kernel_evidence_fails_explicit_isolation_gate(self):
        archives=self.signed_provenance_archives(['a'*64,'a'*64])
        with self.assertRaisesRegex(ValueError,'SAME_KERNEL_OBSERVED'):
            collect(archives,self.root/'collected',self.pins,self.session,self.manifest,
                    require_distinct_kernels=True)
        self.assertFalse((self.root/'collected').exists())

    def test_missing_kernel_evidence_fails_explicit_isolation_gate(self):
        with self.assertRaisesRegex(ValueError,'UNKNOWN_MISSING'):
            collect(self.archives,self.root/'collected',self.pins,self.session,self.manifest,
                    require_distinct_kernels=True)

    def test_distinct_signed_kernel_reports_are_not_hardware_attestation(self):
        archives=self.signed_provenance_archives(['a'*64,'b'*64])
        result=collect(archives,self.root/'collected',self.pins,self.session,self.manifest,
                       require_distinct_kernels=True)
        self.assertEqual(result['host_evidence']['status'],'DISTINCT_KERNEL_REPORTS_UNVERIFIED')
        self.assertIn('not',result['host_evidence']['measurement_note'].lower())

    def test_same_kernel_import_permitted_when_unverified_but_explicitly_flagged(self):
        archives=self.signed_provenance_archives(['a'*64,'a'*64])
        result=self.accept(archives=archives)
        self.assertEqual(result['host_evidence']['status'],'SAME_KERNEL_OBSERVED')
        self.assertEqual(result['signed_host_evidence']['status'],
                         'ALL_PINNED_SIGNING_KEYS_VERIFIED_NOT_HOST_ATTESTATION')

    def test_second_acceptance_cannot_overwrite_first(self):
        report=self.accept()
        with self.assertRaisesRegex(ValueError,'Destination'):
            self.accept()
        self.assertEqual(json.loads((self.root/'collected'/'collection.json').read_text()),report)

    def test_partial_clock_reports_refuse(self):
        c=self.root/'clock-0.json';c.write_text(json.dumps({'run_id':'signed-ci-run','shard_index':0}))
        archive=self.root/'partial-clock.zip'
        bundle(self.folders[0],self.root/'signed-0.json',archive,clock=c)
        with self.assertRaisesRegex(ValueError,'Partial clock'):
            self.accept(archives=[archive,self.archives[1]])

    def test_cli_real_child_process(self):
        for name,obj in [('pins.json',self.pins),('session.json',self.session),('manifest.json',self.manifest)]:
            (self.root/name).write_text(json.dumps(obj))
        proc=subprocess.run([sys.executable,str(SIM/'shard_collection.py'),
             '--pins',str(self.root/'pins.json'),'--session',str(self.root/'session.json'),
             '--manifest',str(self.root/'manifest.json'),'--output-dir',str(self.root/'ci-out'),
             *(str(x) for x in self.archives[::-1])],capture_output=True,text=True,timeout=20)
        self.assertEqual(proc.returncode,0,proc.stderr)
        self.assertIn('NOT_GLOBAL_WORKLOAD',proc.stdout)

    def test_publish_refuses_to_rename_over_path_created_during_validation(self):
        import shard_collection
        real=shard_collection._publish_no_replace
        destination=self.root/'collected'
        def race(stage,target):
            target.mkdir()
            (target/'sentinel').write_text('from-race')
            real(stage,target)
        shard_collection._publish_no_replace=race
        try:
            with self.assertRaises(FileExistsError):self.accept()
        finally:
            shard_collection._publish_no_replace=real
        self.assertEqual((destination/'sentinel').read_text(),'from-race')
        self.assertEqual(list(self.root.glob('.deroyque-collect-*')),[])

if __name__=='__main__':unittest.main()

class RealMergeImportedCollectionTests(unittest.TestCase):
    """Collector output is accepted by unchanged global deterministic workload audit."""
    def setUp(self):
        sys.path.insert(0, str(SIM / "tests"))
        from test_distributed_merge import MergeTests, PLAN
        self.fixture=MergeTests('test_complete_global_reconciliation')
        self.fixture.setUp();self.addCleanup(self.fixture.tearDown)
        self.root=self.fixture.root
        plan={**PLAN,'heap_max_mb':32,'concurrency_per_jvm':8}
        self.manifest={'schema_version':2,'run_id':'testm7','target_url':'http://127.0.0.1:18081',
              'shards':2,'jvms_per_host':[1,1], 'global_posts':12,'shared_keys':4,
              'start_at_epoch_ms':plan['start_at_ms'], 'launch_plan':plan}
        self.pins={};self.keys=[]
        for i,folder in enumerate(self.fixture.paths):
            info=json.loads((folder/'summary.json').read_text())
            info['plan'].update(heap_max_mb=32,concurrency_per_jvm=8)
            (folder/'summary.json').write_text(json.dumps(info))
            key=Ed25519PrivateKey.generate()
            private=self.root/f'full-key-{i}.pem'
            private.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
            private.chmod(0o600)
            self.keys.append(private)
            self.pins[f'host-{i}']=key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        self.session=create_session(self.manifest,self.pins,['host-0','host-1'])
        self.archives=[]
        for i,folder in enumerate(self.fixture.paths):
            envelope=self.root/f'valid-{i}.json'
            envelope.write_text(json.dumps(make_envelope(folder,'testm7',f'host-{i}',i,
                self.keys[i],self.session,self.manifest,self.pins)))
            bundle_file=self.root/f'valid-{i}.zip'
            bundle(folder,envelope,bundle_file)
            self.archives.append(bundle_file)

    def test_offline_canonical_zip_to_verified_merge_and_signed_report(self):
        from distributed_merge import merge
        (self.root/'pins.json').write_text(json.dumps(self.pins))
        (self.root/'session.json').write_text(json.dumps(self.session))
        (self.root/'manifest.json').write_text(json.dumps(self.manifest))
        imported=self.root/'verified-import'
        record=collect(self.archives[::-1],imported,self.pins,self.session,self.manifest)
        shards=[imported/f'shard-{i:03d}' for i in range(2)]
        result=merge(shards,launch_manifest=self.root/'manifest.json',
            host_pins=self.root/'pins.json',signed_host_reports=[f/'signed-envelope.json' for f in shards],
            host_session=self.root/'session.json')
        self.assertEqual(result['outcome'],'PASS',result.get('harness_errors'))
        self.assertEqual(result['global_recorded_posts'],12)
        self.assertEqual(len(record['bundles']),2)

    def tearDown(self):
        # The global merger deliberately reads trusted files independently.
        pass
