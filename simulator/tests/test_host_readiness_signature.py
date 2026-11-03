"""Real Ed25519 host signing, hostile evidence, and signed preflight matrix tests."""
import base64
import copy
import json
import os
import pathlib
import stat
import subprocess
import sys
import tempfile
import time
import unittest

SIM=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SIM))
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PrivateFormat,PublicFormat,NoEncryption
from distributed_prepare import parse_args,build_scripts
from host_session import create_session,digest
from host_matrix import audit
from host_readiness import check
from host_readiness_signature import make_proof,verify_proofs,_read_regular,_strict_document


class SignedReadinessTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory(prefix='signed-preflight-')
        self.addCleanup(tmp.cleanup)
        self.root=pathlib.Path(tmp.name)
        self.now=int(time.time()*1000)
        args=parse_args(['--url','http://127.0.0.1:18081','--shards','2',
             '--jvms-per-host','1','--requests','8','--keys','2',
             '--run-id','signed-preflight','--start-after-seconds','180',
             '--output-dir',str(self.root/'unused')])
        scripts,self.manifest=build_scripts(args,now_ms=self.now)
        self.pins={};self.private=[];self.reports=[]
        for i in range(2):
            launcher=self.root/f'launch-host-{i:03d}.sh';launcher.write_text(scripts[launcher.name])
            d=check(self.manifest,i,launcher,now_ms=self.now)
            d['kernel_fingerprint']=str(i+1)*64
            report=self.root/f'ready-{i}.json';report.write_text(json.dumps(d,sort_keys=True)+'\n')
            self.reports.append(report)
            key=Ed25519PrivateKey.generate();raw=key.private_bytes(Encoding.PEM,PrivateFormat.PKCS8,NoEncryption())
            path=self.root/f'private-{i}.pem';path.write_bytes(raw);path.chmod(0o600);self.private.append(path)
            self.pins[f'host-{i}']=key.public_key().public_bytes(Encoding.PEM,PublicFormat.SubjectPublicKeyInfo).decode()
        self.session=create_session(self.manifest,self.pins,['host-0','host-1'])
        self.proofs=[]
        for i in range(2):
            proof=make_proof(self.reports[i],self.private[i],f'host-{i}',self.session,self.manifest,self.pins)
            path=self.root/f'proof-{i}.json';path.write_text(json.dumps(proof,sort_keys=True)+'\n')
            self.proofs.append(path)

    def audit_signed(self,reports=None,proofs=None,session=None,pins=None,manifest=None):
        return audit(manifest if manifest is not None else self.manifest,
                     reports if reports is not None else self.reports,
                     signed_readiness_paths=proofs if proofs is not None else self.proofs,
                     session=session if session is not None else self.session,
                     pins=pins if pins is not None else self.pins,
                     strict_temporal=True,now_ms=self.now)

    def test_valid_two_real_signers_in_unordered_shard_arrival(self):
        result=self.audit_signed(self.reports[::-1],self.proofs[::-1])
        self.assertEqual(result['signed_readiness']['status'],'ALL_PINNED_KEYS_VERIFIED_NOT_HOST_ATTESTATION')
        self.assertEqual(result['signed_readiness']['session_sha256'],digest(self.session))
        self.assertEqual([x['shard_index'] for x in result['signed_readiness']['verified_shards']],[0,1])
        self.assertEqual(result['signed_readiness']['verified_shards'][0]['readiness_sha256'],
                         __import__('hashlib').sha256(self.reports[0].read_bytes()).hexdigest())

    def test_untrusted_report_single_byte_mutation_rejected(self):
        self.reports[1].write_bytes(self.reports[1].read_bytes()+b' ')
        with self.assertRaisesRegex(ValueError,'content/session/host'):self.audit_signed()

    def test_valid_json_reorder_requires_new_signature(self):
        data=json.loads(self.reports[0].read_text());self.reports[0].write_text(json.dumps(data,indent=4))
        with self.assertRaisesRegex(ValueError,'content/session/host'):self.audit_signed()

    def test_changed_report_semantics_do_not_become_trusted_by_new_signing(self):
        doc=json.loads(self.reports[0].read_text());doc['configured_java_heap_total_bytes']=1
        self.reports[0].write_text(json.dumps(doc))
        proof=make_proof(self.reports[0],self.private[0],'host-0',self.session,self.manifest,self.pins)
        self.proofs[0].write_text(json.dumps(proof))
        with self.assertRaisesRegex(ValueError,'runtime or resource'):self.audit_signed()

    def test_replay_old_proof_into_fresh_collector_challenge_rejected(self):
        session=create_session(self.manifest,self.pins,['host-0','host-1'])
        with self.assertRaisesRegex(ValueError,'content/session/host'):self.audit_signed(session=session)

    def test_different_manifest_rejected(self):
        changed=dict(self.manifest,shared_keys=self.manifest['shared_keys']+1)
        with self.assertRaisesRegex(ValueError,'manifest'):self.audit_signed(manifest=changed)

    def test_swapped_host_key_cannot_impersonate_assigned_host(self):
        with self.assertRaisesRegex(ValueError,'differs'):make_proof(
            self.reports[0],self.private[1],'host-0',self.session,self.manifest,self.pins)

    def test_swapped_proof_bodies_rejected(self):
        proofs=[json.loads(x.read_text()) for x in self.proofs]
        proofs[0]['payload']['host_id']='host-1'
        self.proofs[0].write_text(json.dumps(proofs[0]))
        with self.assertRaisesRegex(ValueError,'content/session/host'):self.audit_signed()

    def test_swapped_envelopes_in_input_order_are_valid(self):
        self.assertIn('VERIFIED',self.audit_signed(proofs=self.proofs[::-1])['signed_readiness']['status'])

    def test_duplicate_shard_proof_rejected(self):
        with self.assertRaisesRegex(ValueError,'Duplicate'):self.audit_signed(proofs=[self.proofs[0],self.proofs[0]])

    def test_incomplete_bundle_rejected(self):
        with self.assertRaisesRegex(ValueError,'complete|Complete'):self.audit_signed(proofs=self.proofs[:1])

    def test_no_partial_downgrade_or_in_memory_signature(self):
        with self.assertRaisesRegex(ValueError,'complete'):audit(self.manifest,self.reports,session=self.session)
        with self.assertRaisesRegex(ValueError,'exact report files'):self.audit_signed(reports=[json.loads(x.read_text()) for x in self.reports])

    def test_noncanonical_signature_rejected(self):
        proof=json.loads(self.proofs[0].read_text());proof['signature']=proof['signature'].replace('=','')
        self.proofs[0].write_text(json.dumps(proof))
        with self.assertRaisesRegex(ValueError,'signature'):self.audit_signed()

    def test_invalid_signature_bits_rejected(self):
        proof=json.loads(self.proofs[0].read_text());sig=bytearray(base64.b64decode(proof['signature']));sig[0]^=1
        proof['signature']=base64.b64encode(sig).decode()
        self.proofs[0].write_text(json.dumps(proof))
        with self.assertRaisesRegex(ValueError,'Invalid pinned-host'):self.audit_signed()

    def test_nested_duplicate_keys_rejected(self):
        text=self.proofs[0].read_text().replace('"schema_version": 1','"schema_version": 1, "schema_version": 1')
        self.proofs[0].write_text(text)
        with self.assertRaisesRegex(ValueError,'Duplicate'):self.audit_signed()

    def test_symlink_and_fifo_envelope_rejected_safely(self):
        link=self.root/'link.json';link.symlink_to(self.proofs[0])
        with self.assertRaises(OSError):self.audit_signed(proofs=[link,self.proofs[1]])
        fifo=self.root/'fifo';os.mkfifo(fifo)
        with self.assertRaisesRegex(ValueError,'Unsafe'):self.audit_signed(proofs=[fifo,self.proofs[1]])

    def test_large_or_nonfinite_envelope_rejected(self):
        self.proofs[0].write_text(' '*16385)
        with self.assertRaisesRegex(ValueError,'oversized'):self.audit_signed()
        self.proofs[0].write_text('{"payload": NaN, "signature": "bad"}')
        with self.assertRaisesRegex(ValueError,'Nonfinite'):self.audit_signed()

    def test_private_key_must_be_owner_only_regular(self):
        self.private[0].chmod(0o644)
        with self.assertRaisesRegex(ValueError,'owner-only'):make_proof(
            self.reports[0],self.private[0],'host-0',self.session,self.manifest,self.pins)
        self.private[0].chmod(0o600)
        fake=self.root/'key-link';fake.symlink_to(self.private[0])
        with self.assertRaises(OSError):make_proof(self.reports[0],fake,'host-0',self.session,self.manifest,self.pins)

    def test_wrong_signer_identity_or_role_rejected(self):
        with self.assertRaisesRegex(ValueError,'assignment'):make_proof(
            self.reports[0],self.private[0],'host-1',self.session,self.manifest,self.pins)

    def test_even_signed_same_kernel_does_not_pass(self):
        doc=json.loads(self.reports[1].read_text());doc['kernel_fingerprint']=json.loads(self.reports[0].read_text())['kernel_fingerprint']
        self.reports[1].write_text(json.dumps(doc))
        self.proofs[1].write_text(json.dumps(make_proof(
            self.reports[1],self.private[1],'host-1',self.session,self.manifest,self.pins)))
        with self.assertRaisesRegex(ValueError,'SAME_KERNEL'):self.audit_signed()

    def test_real_sign_cli_and_matrix_cli_one_time_output(self):
        for name, obj in [('manifest',self.manifest),('pins',self.pins),('session',self.session)]:
            (self.root/f'{name}.json').write_text(json.dumps(obj))
        out=self.root/'proof-new.json'
        cmd=[sys.executable,str(SIM/'host_readiness_signature.py'),
             '--report',str(self.reports[0]),'--private-key',str(self.private[0]),
             '--host-id','host-0','--session',str(self.root/'session.json'),
             '--manifest',str(self.root/'manifest.json'),'--pins',str(self.root/'pins.json'),
             '--output',str(out)]
        proc=subprocess.run(cmd,capture_output=True,text=True,timeout=15)
        self.assertEqual(proc.returncode,0,proc.stderr)
        self.assertEqual(stat.S_IMODE(out.stat().st_mode),0o600)
        self.assertEqual(subprocess.run(cmd,capture_output=True,timeout=15).returncode,2)
        target=self.root/'verdict.json'
        cmd=[sys.executable,str(SIM/'host_matrix.py'),
             '--manifest',str(self.root/'manifest.json'),
             '--host-pins',str(self.root/'pins.json'),
             '--host-session',str(self.root/'session.json'),
             '--signed-readiness',str(out),'--signed-readiness',str(self.proofs[1]),
             '--output',str(target),str(self.reports[1]),str(self.reports[0])]
        proc=subprocess.run(cmd,capture_output=True,text=True,timeout=15)
        self.assertEqual(proc.returncode,0,proc.stderr)
        self.assertEqual(json.loads(target.read_text())['signed_readiness']['status'],
                         'ALL_PINNED_KEYS_VERIFIED_NOT_HOST_ATTESTATION')
        self.assertEqual(subprocess.run(cmd,capture_output=True,timeout=15).returncode,2)

if __name__=='__main__':unittest.main()
