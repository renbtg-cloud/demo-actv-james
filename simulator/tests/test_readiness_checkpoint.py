"""Independent collector key: exact matrix, session, no-downgrade checks."""
import base64
import json
import os
import pathlib
import stat
import subprocess
import sys
import unittest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding,PrivateFormat,PublicFormat,NoEncryption

SIM=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SIM))
from readiness_checkpoint import sign_checkpoint,verify_checkpoint
from host_session import create_session
from distributed_merge import merge

class CheckpointTests(unittest.TestCase):
    def setUp(self):
        from test_readiness_handoff import HandoffTests
        self.lab=HandoffTests('test_real_signed_handoff_bound_to_two_provenance_chains')
        self.lab.setUp();self.addCleanup(self.lab.doCleanups)
        self.root=self.lab.root
        key=Ed25519PrivateKey.generate()
        self.private=self.root/'collector-private.pem'
        self.private.write_bytes(key.private_bytes(Encoding.PEM,PrivateFormat.PKCS8,NoEncryption()))
        self.private.chmod(0o600)
        self.public=self.root/'collector-public.pem'
        self.public.write_bytes(key.public_key().public_bytes(Encoding.PEM,PublicFormat.SubjectPublicKeyInfo))
        self.receipt=self.root/'checkpoint.json'
        self.sign()

    def sign(self):
        self.receipt.write_text(json.dumps(sign_checkpoint(self.lab.matrix,self.lab.report_paths,
            self.lab.proof_paths,self.lab.lab.session,self.lab.lab.manifest,
            self.lab.lab.pins,self.private)))

    def verify(self,**kwargs):
        args={'matrix_path':self.lab.matrix,'receipt_path':self.receipt,
              'public_key_path':self.public,'report_paths':self.lab.report_paths,
              'proof_paths':self.lab.proof_paths,'session':self.lab.lab.session,
              'manifest':self.lab.lab.manifest,'pins':self.lab.lab.pins}
        args.update(kwargs)
        return verify_checkpoint(**args)

    def merged(self,**kwargs):
        return self.lab.result(preflight_checkpoint=kwargs.get('receipt',self.receipt),
                               collector_public_key=kwargs.get('public',self.public))

    def test_independently_pinned_collector_signature_verified(self):
        v=self.verify()
        self.assertEqual(v['status'],'PINNED_COLLECTOR_CHECKPOINT_VERIFIED_NOT_TRUSTED_TIME_OR_HARDWARE_ATTESTATION')
        r=self.merged()
        self.assertEqual(r['outcome'],'PASS',r['harness_errors'])
        self.assertEqual(r['readiness_handoff']['collector_checkpoint'],v)

    def test_wrong_collector_public_key_is_rejected(self):
        wrong=Ed25519PrivateKey.generate()
        self.public.write_bytes(wrong.public_key().public_bytes(Encoding.PEM,PublicFormat.SubjectPublicKeyInfo))
        with self.assertRaisesRegex(ValueError,'signature'):self.verify()
        self.assertEqual(self.merged()['outcome'],'FAIL')

    def test_matrix_whitespace_is_signed_exactly(self):
        self.lab.matrix.write_bytes(self.lab.matrix.read_bytes()+b'\n')
        with self.assertRaisesRegex(ValueError,'exact preflight/session bytes'):self.verify()
        self.assertEqual(self.merged()['outcome'],'FAIL')

    def test_checkpoint_signature_bit_flip_rejected(self):
        doc=json.loads(self.receipt.read_text())
        raw=bytearray(base64.b64decode(doc['signature']));raw[-1]^=1
        doc['signature']=base64.b64encode(raw).decode()
        self.receipt.write_text(json.dumps(doc))
        with self.assertRaisesRegex(ValueError,'signature'):self.verify()

    def test_duplicate_json_field_rejected(self):
        data=self.receipt.read_text().replace('"schema_version": 1','"schema_version": 1, "schema_version": 1')
        self.receipt.write_text(data)
        with self.assertRaisesRegex(ValueError,'Duplicate'):self.verify()

    def test_changed_policy_does_not_pass_even_if_receipt_still_valid(self):
        doc=json.loads(self.lab.matrix.read_text());doc['network_probed_for_all']=not doc['network_probed_for_all']
        self.lab.matrix.write_text(json.dumps(doc))
        with self.assertRaises(ValueError):self.verify()

    def test_replayed_challenge_is_rejected(self):
        session=create_session(self.lab.lab.manifest,self.lab.lab.pins,['host-0','host-1'])
        with self.assertRaisesRegex(ValueError,'verdict|session|readiness'):self.verify(session=session)

    def test_oversized_receipt_rejected(self):
        self.receipt.write_bytes(b'X'*16385)
        with self.assertRaisesRegex(ValueError,'oversized'):self.verify()

    def test_symlink_receipt_is_rejected(self):
        link=self.root/'linked-checkpoint';link.symlink_to(self.receipt)
        with self.assertRaises(OSError):self.verify(receipt_path=link)

    def test_fifo_receipt_nonblocking_rejection(self):
        fifo=self.root/'fifo-checkpoint';os.mkfifo(fifo)
        with self.assertRaisesRegex(ValueError,'Unsafe'):self.verify(receipt_path=fifo)

    def test_incomplete_checkpoint_option_not_a_downgrade(self):
        self.assertEqual(self.lab.result(preflight_checkpoint=self.receipt)['outcome'],'FAIL')
        self.assertEqual(self.lab.result(collector_public_key=self.public)['outcome'],'FAIL')

    def test_genuine_collector_signature_cannot_override_invalid_postrun_proof(self):
        path=self.lab.lab.reports[0];doc=json.loads(path.read_text())
        doc['payload']['summary_sha256']='0'*64
        path.write_text(json.dumps(doc))
        self.assertEqual(self.merged()['outcome'],'FAIL')

    def test_signing_key_must_remain_owner_only(self):
        self.private.chmod(0o644)
        with self.assertRaisesRegex(ValueError,'owner-only'):sign_checkpoint(
            self.lab.matrix,self.lab.report_paths,self.lab.proof_paths,
            self.lab.lab.session,self.lab.lab.manifest,self.lab.lab.pins,self.private)

    def test_real_sign_verify_cli_and_no_overwrite(self):
        self.receipt.unlink()
        common=['--matrix',str(self.lab.matrix),
                '--report',str(self.lab.report_paths[0]),'--report',str(self.lab.report_paths[1]),
                '--signed-readiness',str(self.lab.proof_paths[0]),
                '--signed-readiness',str(self.lab.proof_paths[1]),
                '--session',str(self.lab.lab.session_path),
                '--manifest',str(self.lab.lab.manifest_path),
                '--pins',str(self.lab.lab.pins_path),'--receipt',str(self.receipt)]
        base=[sys.executable,str(SIM/'readiness_checkpoint.py')]
        a=subprocess.run(base+['sign',*common,'--collector-key',str(self.private)],capture_output=True,text=True,timeout=20)
        self.assertEqual(a.returncode,0,a.stderr)
        self.assertEqual(stat.S_IMODE(self.receipt.stat().st_mode),0o600)
        b=subprocess.run(base+['verify',*common,'--collector-key',str(self.public)],capture_output=True,text=True,timeout=20)
        self.assertEqual(b.returncode,0,b.stderr)
        self.assertEqual(subprocess.run(base+['sign',*common,'--collector-key',str(self.private)],capture_output=True,timeout=20).returncode,2)

    def test_wrong_key_algorithm_refused(self):
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.serialization import PrivateFormat
        ec_key=ec.generate_private_key(ec.SECP256R1())
        self.public.write_bytes(ec_key.public_key().public_bytes(Encoding.PEM,PublicFormat.SubjectPublicKeyInfo))
        with self.assertRaisesRegex(ValueError,'Ed25519'):self.verify()

    def test_swapped_host_role_still_rejected_with_valid_collector_receipt(self):
        r=self.merged()
        self.assertEqual(r['outcome'],'PASS')
        doc=json.loads(self.lab.lab.reports[1].read_text())
        doc['payload']['host_id']='host-0'
        self.lab.lab.reports[1].write_text(json.dumps(doc))
        self.assertEqual(self.merged()['outcome'],'FAIL')

if __name__=='__main__':unittest.main()
