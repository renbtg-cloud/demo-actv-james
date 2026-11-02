import base64
import sys
from pathlib import Path
import unittest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
sys.path.insert(0,str(Path(__file__).resolve().parent))
from gateway_signature_audit import verify_proof, signed_tokens_query
RUN='gw-good123'
KEY=RUN+'-0'
STAMP=1793641000000
NONCE='n-unique'

def b64(x):return base64.urlsafe_b64encode(x).rstrip(b'=').decode()

def fake_proof(private,key=KEY,nonce=NONCE,stamp=STAMP):
    data='\n'.join(['P20V1','deroyque-lab-provider','lab-key-01',b64(key.encode()),'ACTIVATED','1','1',str(stamp),nonce]).encode()
    return b64(data)+'.'+b64(private.sign(data))

class SignatureProofTests(unittest.TestCase):
    def setUp(self):
        self.key=Ed25519PrivateKey.generate();self.pub=self.key.public_key()
        self.token=fake_proof(self.key)

    def test_valid_ed25519(self):
        self.assertTrue(verify_proof(self.token,KEY,NONCE,STAMP,self.pub))

    def test_valid_signature_for_wrong_key(self):
        t=fake_proof(self.key,key=KEY+'x')
        with self.assertRaises(ValueError):verify_proof(t,KEY,NONCE,STAMP,self.pub)

    def test_valid_signature_for_wrong_nonce(self):
        t=fake_proof(self.key,nonce='different')
        with self.assertRaises(ValueError):verify_proof(t,KEY,NONCE,STAMP,self.pub)

    def test_wrong_stored_timestamp(self):
        with self.assertRaises(ValueError):verify_proof(self.token,KEY,NONCE,STAMP+1,self.pub)

    def test_wrong_private_key(self):
        with self.assertRaises(ValueError):verify_proof(self.token,KEY,NONCE,STAMP,Ed25519PrivateKey.generate().public_key())

    def test_signature_mutation(self):
        h,s=self.token.split('.');altered=s[:-1]+('A' if s[-1]!='A' else 'B')
        with self.assertRaises(ValueError):verify_proof(h+'.'+altered,KEY,NONCE,STAMP,self.pub)

    def test_reject_empty_and_malformed(self):
        for t in ['',self.token+'.extra','%%%.'+'a'*86,'a.b']:
            with self.subTest(t=t[:12]),self.assertRaises(ValueError):verify_proof(t,KEY,NONCE,STAMP,self.pub)

    def test_query_is_readonly(self):
        q=signed_tokens_query('gw_abcdef',RUN)
        self.assertIn('a.token',q)
        self.assertNotIn('DELETE ',q)

if __name__=='__main__':unittest.main()
