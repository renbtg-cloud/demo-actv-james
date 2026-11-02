"""End-to-end collector-signed exact-result evidence; reject tamper and forgery."""
import base64
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

SIM=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SIM))
from host_session import digest
from collector_receipt import create_receipt,verify_receipt

class ReceiptTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup)
        self.root=pathlib.Path(t.name)
        self.session={'schema_version':1,'run_id':'signed-test', 'nonce':'a'*64,
          'host_assignments':['host-0','host-1'],'manifest_sha256':'b'*64,'pins_sha256':'c'*64}
        self.session_file=self.root/'session.json';self.session_file.write_text(json.dumps(self.session))
        self.verdict={'outcome':'PASS','run_id':'signed-test','session_sha256':digest(self.session),
          'harness_errors':[],'global_invariant_violations':[], 'state_verification_errors':[],
          'unexpected_http_or_transport':0,
          'signed_host_evidence':{'status':'ALL_PINNED_SIGNING_KEYS_VERIFIED_NOT_HOST_ATTESTATION'}}
        self.result_file=self.root/'verdict.json';self.result_file.write_text(json.dumps(self.verdict))
        key=Ed25519PrivateKey.generate()
        self.private=self.root/'collector-private.pem'
        self.private.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                          serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        self.private.chmod(0o600)
        self.public=self.root/'collector-public.pem'
        self.public.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM,
                                serialization.PublicFormat.SubjectPublicKeyInfo))
        self.receipt_file=self.root/'receipt.json'

    def sign(self):
        result=create_receipt(self.result_file,self.session_file,self.private)
        self.receipt_file.write_text(json.dumps(result))
        return result

    def verify(self):
        return verify_receipt(self.result_file,self.session_file,self.receipt_file,self.public)

    def test_real_ed25519_receipt_verifies_exact_bytes(self):
        self.sign()
        self.assertEqual(self.verify()['status'],'COLLECTOR_RECEIPT_VERIFIED_NOT_HARDWARE_ATTESTATION')

    def test_reject_whitespace_tampering_in_exact_verdict_bytes(self):
        self.sign()
        self.result_file.write_bytes(self.result_file.read_bytes()+b' ')
        with self.assertRaises(ValueError):self.verify()

    def test_reject_changed_completed_result(self):
        self.sign()
        report=dict(self.verdict,global_recorded_posts=999)
        self.result_file.write_text(json.dumps(report))
        with self.assertRaises(ValueError):self.verify()

    def test_reject_changed_session_challenge(self):
        self.sign()
        self.session_file.write_text(json.dumps(dict(self.session,nonce='d'*64)))
        with self.assertRaises(ValueError):self.verify()

    def test_reject_wrong_collector_public_key(self):
        self.sign()
        other=Ed25519PrivateKey.generate()
        self.public.write_bytes(other.public_key().public_bytes(
                     serialization.Encoding.PEM,serialization.PublicFormat.SubjectPublicKeyInfo))
        with self.assertRaises(ValueError):self.verify()

    def test_reject_altered_signature(self):
        env=self.sign()
        sig=bytearray(base64.b64decode(env['signature']));sig[0]^=1
        env['signature']=base64.b64encode(sig).decode()
        self.receipt_file.write_text(json.dumps(env))
        with self.assertRaises(ValueError):self.verify()

    def test_bad_verdict_cannot_get_collector_receipt(self):
        for field,value in [('outcome','FAIL'),('harness_errors',['oops']),
                            ('global_invariant_violations',['x']),('unexpected_http_or_transport',1),
                            ('session_sha256','0'*64)]:
            report=dict(self.verdict,**{field:value})
            self.result_file.write_text(json.dumps(report))
            with self.assertRaises(ValueError):create_receipt(self.result_file,self.session_file,self.private)

    def test_owner_only_key_required(self):
        self.private.chmod(0o644)
        with self.assertRaises(ValueError):create_receipt(self.result_file,self.session_file,self.private)

    def test_real_subprocess_cli_sign_verify_and_no_overwrite(self):
        root=self.root
        sign=[sys.executable,str(SIM/'collector_receipt.py'),'sign',
          '--result',str(self.result_file),'--session',str(self.session_file),
          '--receipt',str(self.receipt_file),'--collector-key',str(self.private)]
        verify=[sys.executable,str(SIM/'collector_receipt.py'),'verify',
          '--result',str(self.result_file),'--session',str(self.session_file),
          '--receipt',str(self.receipt_file),'--collector-key',str(self.public)]
        a=subprocess.run(sign,capture_output=True,text=True,timeout=10)
        self.assertEqual(a.returncode,0,a.stderr)
        b=subprocess.run(verify,capture_output=True,text=True,timeout=10)
        self.assertEqual(b.returncode,0,b.stderr)
        self.assertIn('VERIFIED',b.stdout)
        c=subprocess.run(sign,capture_output=True,text=True,timeout=10)
        self.assertEqual(c.returncode,2)

if __name__=='__main__':unittest.main()
