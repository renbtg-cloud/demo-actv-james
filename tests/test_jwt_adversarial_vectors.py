"""Validate negative CI attack vectors *themselves*, never the application's security."""
import base64
import hashlib
import hmac
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from jwt_adversarial_vectors import claims_for, decode_unverified, hmac_confusion, unsigned
from jwks_rotation_fixture import RotatingJwksFixture


class JwtNegativeVectors(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = RotatingJwksFixture('vectors')
        cls.kid = cls.fixture.add_key('only')
        cls.issuer = 'http://127.0.0.1/test'
        cls.audience = 'test-api'

    @classmethod
    def tearDownClass(cls):
        cls.fixture.close()

    def test_unsigned_is_valid_compact_jwt_with_no_signature(self):
        token=unsigned(claims_for(self.issuer,self.audience),self.kid)
        header,claims=decode_unverified(token)
        self.assertEqual(header['alg'],'none')
        self.assertEqual(claims['aud'],[self.audience])
        self.assertEqual(token.split('.')[2],'')

    def test_rs512_is_cryptographically_valid_but_wrong_algorithm(self):
        token=self.fixture.token(self.kid,self.issuer,self.audience,headers={'alg':'RS512'})
        header,claims=decode_unverified(token)
        self.assertEqual(header['alg'],'RS512')
        self.assertEqual(claims['iss'],self.issuer)
        encoded,signature=token.rsplit('.',1)
        sig=base64.urlsafe_b64decode(signature+'='*(-len(signature)%4))
        self.fixture._private[self.kid].public_key().verify(sig,encoded.encode(),padding.PKCS1v15(),hashes.SHA512())

    def test_hmac_uses_public_information_and_valid_signature(self):
        jwk=self.fixture.public_jwk(self.kid)
        import json
        key=json.dumps(jwk,sort_keys=True,separators=(',',':')).encode()
        token=hmac_confusion(claims_for(self.issuer,self.audience),self.kid,key)
        header,_=decode_unverified(token)
        self.assertEqual(header['alg'],'HS256')
        encoded,mac=token.rsplit('.',1)
        self.assertEqual(base64.urlsafe_b64decode(mac+'='*(-len(mac)%4)),
                         hmac.new(key,encoded.encode(),hashlib.sha256).digest())

    def test_header_extension_does_not_change_signing_identity(self):
        token=self.fixture.token(self.kid,self.issuer,self.audience,
                                 headers={'jku':'http://127.0.0.1:1/malicious'})
        header,_=decode_unverified(token)
        self.assertEqual(header['kid'],self.kid)
        self.assertEqual(header['alg'],'RS256')
        self.assertTrue(header['jku'].endswith('malicious'))

if __name__=='__main__':
    unittest.main()
