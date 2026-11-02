"""Standalone adversarial tests for the disposable JWKS fixture (no Docker)."""
import base64
import json
import secrets
import unittest
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from jwks_rotation_fixture import RotatingJwksFixture
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa


def keys(url):
    with urllib.request.urlopen(url, timeout=3) as response:
        return json.load(response)['keys']


class JwksFixtureTests(unittest.TestCase):
    def setUp(self):
        self.fixture = RotatingJwksFixture(secrets.token_hex(5))

    def tearDown(self):
        self.fixture.close()

    def test_rotation_overlap_and_retirement(self):
        a = self.fixture.add_key('a')
        b = self.fixture.add_key('b')
        self.assertEqual({k['kid'] for k in keys(self.fixture.uri)}, {a,b})
        self.fixture.retire(a)
        self.assertEqual({k['kid'] for k in keys(self.fixture.uri)}, {b})
        with self.assertRaises(ValueError):
            self.fixture.add_key('a')
        with self.assertRaises(ValueError):
            self.fixture.publish('unrecognized-key')

    def test_fail_closed_outage_and_recovery(self):
        a = self.fixture.add_key('a', publish=False)
        self.assertEqual(keys(self.fixture.uri), [])
        self.fixture.available(False)
        with self.assertRaises(urllib.error.HTTPError) as caught:
            keys(self.fixture.uri)
        self.assertEqual(caught.exception.code,503)
        self.fixture.available(True)
        self.fixture.publish(a)
        self.assertEqual(keys(self.fixture.uri)[0]['kid'],a)
        self.assertGreaterEqual(self.fixture.get_count,3)

    def test_generated_jwt_signature_matches_only_published_key(self):
        a = self.fixture.add_key('a')
        issuer='http://127.0.0.1/test-issuer'
        jwt = self.fixture.token(a,issuer,'expected-api',scope='activation:read')
        header,claims,signature=jwt.split('.')
        decode=lambda s: base64.urlsafe_b64decode(s+'='*(-len(s)%4))
        parsed_header=json.loads(decode(header))
        parsed_claims=json.loads(decode(claims))
        self.assertEqual(parsed_header['kid'],a)
        self.assertEqual(parsed_header['alg'],'RS256')
        self.assertEqual(parsed_claims['aud'],['expected-api'])
        self.assertEqual(parsed_claims['scope'],'activation:read')
        self.assertEqual(parsed_claims['iss'],issuer)
        jwk=keys(self.fixture.uri)[0]
        number=lambda x:int.from_bytes(decode(jwk[x]),'big')
        key=rsa.RSAPublicNumbers(number('e'),number('n')).public_key()
        key.verify(decode(signature),(header+'.'+claims).encode(),padding.PKCS1v15(),hashes.SHA256())
        self.assertNotIn('d',jwk)
        self.assertNotIn('p',jwk)

    def test_parallel_reads_during_rotation_remain_parseable(self):
        self.fixture.add_key('first')
        def worker(_):return keys(self.fixture.uri)
        with ThreadPoolExecutor(max_workers=8) as executor:
            def batches():
                for seq in range(4):
                    self.fixture.add_key('rot-'+str(seq))
                    yield from executor.map(worker,range(12))
            snapshots=list(batches())
        self.assertEqual(len(snapshots),48)
        self.assertTrue(all(1<=len(snapshot)<=5 for snapshot in snapshots))
        self.assertEqual(len(keys(self.fixture.uri)),5)

if __name__=='__main__':
    unittest.main()
