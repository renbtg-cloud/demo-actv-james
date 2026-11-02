"""Actual Ed25519 transfer + adversarial ZIP mutation tests (no SSH/VM claims)."""
import hashlib
import json
import os
import pathlib
import stat
import subprocess
import sys
import tempfile
import unittest
import zipfile
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

SIM=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SIM))
from host_signed_evidence import make_envelope,verify_envelopes
from shard_bundle import bundle,inspect_archive,unpack

class BundleTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(prefix='bundle-test-');self.addCleanup(tmp.cleanup)
        self.root=pathlib.Path(tmp.name)
        self.hosts=[];self.pins={};self.reports=[];self.bundles=[]
        for i in range(2):
            folder=self.root / f'shard-{i}';folder.mkdir()
            (folder/'summary.json').write_text(json.dumps({'run_id':'bundle_run','plan':{'shard_index':i,'jvms':2}}))
            for j in range(2):
                (folder/f'agent-{j:03d}.tsv').write_text(f'row-{i}-{j}\n')
            key=Ed25519PrivateKey.generate()
            keyfile=self.root/f'private-{i}.pem'
            keyfile.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
            keyfile.chmod(0o600)
            self.pins[f'host-{i}']=key.public_key().public_bytes(serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo).decode()
            signed=self.root/f'signed-{i}.json'
            signed.write_text(json.dumps(make_envelope(folder,'bundle_run',f'host-{i}',i,keyfile)))
            result=self.root/f'bundle-{i}.zip'
            bundle(folder,signed,result)
            self.hosts.append(folder);self.reports.append(signed);self.bundles.append(result)

    def rewrite(self, idx, change):
        original=self.bundles[idx]
        with zipfile.ZipFile(original) as f:
            entries=[(item, f.read(item)) for item in f.infolist()]
        with zipfile.ZipFile(original, 'w') as f:
            for item, value in change(entries):
                f.writestr(item, value)

    def test_roundtrip_offline_two_signed_hosts_and_reversed_delivery_order(self):
        transported=[]
        for i in (1,0):
            self.assertEqual(inspect_archive(self.bundles[i])['shard_index'],i)
            dest=self.root/f'received-{i}'
            unpack(self.bundles[i],dest)
            transported.append(dest)
        result,errors=verify_envelopes(self.pins,[f/'signed-envelope.json' for f in transported],transported,'bundle_run')
        self.assertEqual(errors,[])
        self.assertEqual(result['status'],'ALL_PINNED_SIGNING_KEYS_VERIFIED_NOT_HOST_ATTESTATION')

    def test_same_bytes_reproduce_identical_zip_hash(self):
        another=self.root/'duplicate.zip'
        bundle(self.hosts[0],self.reports[0],another)
        self.assertEqual(hashlib.sha256(self.bundles[0].read_bytes()).hexdigest(),hashlib.sha256(another.read_bytes()).hexdigest())

    def test_no_private_key_in_transport(self):
        with zipfile.ZipFile(self.bundles[0]) as f:
            self.assertNotIn('private-0.pem',f.namelist())
            self.assertEqual(f.namelist()[-1],'bundle-manifest.json')

    def test_reject_existing_archive(self):
        with self.assertRaisesRegex(ValueError,'Destination'):
            bundle(self.hosts[0],self.reports[0],self.bundles[0])

    def test_reject_existing_destination(self):
        dest=self.root/'existing';dest.mkdir()
        with self.assertRaisesRegex(ValueError,'Destination'):
            unpack(self.bundles[0],dest)

    def test_reject_extra_agent_source(self):
        (self.hosts[0]/'agent-002.tsv').write_text('extra')
        with self.assertRaisesRegex(ValueError,'unexpected agent'):
            bundle(self.hosts[0],self.reports[0],self.root/'extra.zip')

    def test_reject_symlink_source(self):
        (self.hosts[0]/'agent-000.tsv').unlink()
        (self.hosts[0]/'agent-000.tsv').symlink_to(self.root/'private-0.pem')
        with self.assertRaisesRegex(ValueError,'regular'):
            bundle(self.hosts[0],self.reports[0],self.root/'symlink.zip')

    def test_reject_hardlinked_source(self):
        target=self.hosts[0]/'agent-000.tsv'
        os.link(target,self.root/'agent-clone.tsv')
        with self.assertRaisesRegex(ValueError,'unlinked'):
            bundle(self.hosts[0],self.reports[0],self.root/'hardlink.zip')

    def test_reject_tampered_report_before_export(self):
        e=json.loads(self.reports[0].read_text());e['payload']['run_id']='forged'
        self.reports[0].write_text(json.dumps(e))
        with self.assertRaisesRegex(ValueError,'contradicts'):
            bundle(self.hosts[0],self.reports[0],self.root/'forged.zip')

    def test_reject_transport_tampering(self):
        def change(entries):
            return [(item,(value+b'evil' if item.filename=='agent-000.tsv' else value)) for item,value in entries]
        self.rewrite(0,change)
        with self.assertRaisesRegex(ValueError,'manifest entry|SHA-256'):
            inspect_archive(self.bundles[0])

    def test_reject_modified_manifest(self):
        def change(entries):
            for item,value in entries:
                if item.filename=='bundle-manifest.json':
                    doc=json.loads(value);doc['members']['summary.json']['size']+=1
                    value=json.dumps(doc).encode()
                yield item,value
        self.rewrite(0,change)
        with self.assertRaisesRegex(ValueError,'manifest entry'):
            inspect_archive(self.bundles[0])

    def test_reject_traversal_member(self):
        self.rewrite(0,lambda entries:entries+[(zipfile.ZipInfo('../owned.txt'),b'owned')])
        with self.assertRaisesRegex(ValueError,'Unsafe/unknown'):
            inspect_archive(self.bundles[0])
        self.assertFalse((self.root/'owned.txt').exists())

    def test_reject_absolute_path(self):
        self.rewrite(0,lambda entries:entries+[(zipfile.ZipInfo('/tmp/malicious'),b'malicious')])
        with self.assertRaisesRegex(ValueError,'Unsafe/unknown'):
            inspect_archive(self.bundles[0])

    def test_reject_duplicate_member(self):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter('ignore',UserWarning)
            self.rewrite(0,lambda entries:entries+[(entries[0][0],entries[0][1])])
        with self.assertRaisesRegex(ValueError,'Duplicate'):
            inspect_archive(self.bundles[0])

    def test_reject_zip_symlink(self):
        def change(entries):
            for item,value in entries:
                if item.filename=='agent-000.tsv':
                    item.external_attr=(stat.S_IFLNK|0o777)<<16
                yield item,value
        self.rewrite(0,change)
        with self.assertRaisesRegex(ValueError,'Unsafe archive'):
            inspect_archive(self.bundles[0])

    def test_reject_compressed_member(self):
        def change(entries):
            for item,value in entries:
                if item.filename=='agent-000.tsv':item.compress_type=zipfile.ZIP_DEFLATED
                yield item,value
        self.rewrite(0,change)
        with self.assertRaisesRegex(ValueError,'compression'):
            inspect_archive(self.bundles[0])

    def test_reject_corrupt_zip(self):
        self.bundles[0].write_bytes(b'not a zip')
        with self.assertRaises(zipfile.BadZipFile):inspect_archive(self.bundles[0])

    def test_reject_missing_member(self):
        self.rewrite(0,lambda entries:[(e,d) for e,d in entries if e.filename!='agent-001.tsv'])
        with self.assertRaisesRegex(ValueError,'Missing/extra'):
            inspect_archive(self.bundles[0])

    def test_reject_clock_identity_mismatch(self):
        clock=self.root/'clock.json'
        clock.write_text(json.dumps({'run_id':'other','shard_index':0}))
        with self.assertRaisesRegex(ValueError,'Clock report contradicts'):
            bundle(self.hosts[0],self.reports[0],self.root/'bad-clock.zip',clock)

    def test_changed_valid_bytes_fail_signed_host_verifier(self):
        # A malicious submitter may edit BOTH agent bytes and transport hashes.
        # This must pass transport integrity but fail the independent Ed25519 check.
        signed=self.reports[0]
        (self.hosts[0]/'agent-000.tsv').write_text('different valid bytes')
        target=self.root/'modified.zip'
        bundle(self.hosts[0],signed,target)
        dest=self.root/'malicious-received';unpack(target,dest)
        original_dest=self.root/'normal-received';unpack(self.bundles[1],original_dest)
        _,errors=verify_envelopes(self.pins,[dest/'signed-envelope.json',original_dest/'signed-envelope.json'],[dest,original_dest],'bundle_run')
        self.assertTrue(errors)

    def test_cli_roundtrip(self):
        target=self.root/'cli-result'
        run=subprocess.run([sys.executable,str(SIM/'shard_bundle.py'),'extract','--bundle',str(self.bundles[0]),'--output',str(target)],capture_output=True,text=True,timeout=15)
        self.assertEqual(run.returncode,0,run.stderr)
        self.assertIn('NOT_HOST_AUTHENTICATION',run.stdout)
        self.assertTrue((target/'signed-envelope.json').is_file())

if __name__=='__main__':unittest.main()
