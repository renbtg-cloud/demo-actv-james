"""Synthetic, locally generated JOSE attack vectors for *negative* acceptance tests.

These are not production JWT utilities. No signing material is read from a real
identity provider. The HMAC confusion case deliberately uses the attacker's
chosen public-key bytes as the HMAC secret and is NOT a legitimate RSA token.
"""
import base64
import hashlib
import hmac
import json
import time

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding


def b64url(data):
    return base64.urlsafe_b64encode(data).decode('ascii').rstrip('=')


def compact(header, claims, signature=b''):
    parts = [b64url(json.dumps(obj, separators=(',', ':'), sort_keys=True).encode())
             for obj in (header, claims)]
    signing_input = '.'.join(parts).encode('ascii')
    return signing_input.decode('ascii') + '.' + b64url(signature)


def unsigned(claims, kid):
    return compact({'typ': 'JWT', 'alg': 'none', 'kid': kid}, claims)


def hmac_confusion(claims, kid, public_key_bytes):
    header = {'typ': 'JWT', 'alg': 'HS256', 'kid': kid}
    prefix = compact(header, claims).rsplit('.', 1)[0]
    mac = hmac.new(public_key_bytes, prefix.encode('ascii'), hashlib.sha256).digest()
    return prefix + '.' + b64url(mac)


def claims_for(issuer, audience, scope='activation:read'):
    now = int(time.time())
    return {'iss': issuer, 'aud': [audience], 'scope': scope, 'sub': 'ci-negative-vector',
            'iat': now, 'nbf': now - 10, 'exp': now + 600}


def decode_unverified(token):
    """For local fixture self-checks only. NEVER use for API authorization."""
    parts = token.split('.')
    if len(parts) != 3:
        raise ValueError('not a JWT compact serialization')
    def parse(value):
        return json.loads(base64.urlsafe_b64decode(value + '=' * (-len(value) % 4)))
    return parse(parts[0]), parse(parts[1])
