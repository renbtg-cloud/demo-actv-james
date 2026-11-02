"""Disposable, locally bound JWKS issuer for adversarial Spring JWT acceptance.

Testing ONLY: generated keys, unsigned trust bootstrap via controlled environment,
no authorization-server or production key-management functionality.
"""
import base64
import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa


def b64(value):
    return base64.urlsafe_b64encode(value).decode('ascii').rstrip('=')


class RotatingJwksFixture:
    def __init__(self, tag):
        self._lock = threading.RLock()
        self._private = {}
        self._published = []
        self._available = True
        self._get_count = 0
        self._tag = tag
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path != '/keys':
                    self.send_error(404)
                    return
                with fixture._lock:
                    fixture._get_count += 1
                    available = fixture._available
                    keys = [fixture._jwk(k) for k in fixture._published]
                if not available:
                    self.send_error(503)
                    return
                data = json.dumps({'keys': keys}, separators=(',', ':')).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *unused):
                pass

        self._server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    @property
    def uri(self):
        return f'http://127.0.0.1:{self._server.server_port}/keys'

    @property
    def get_count(self):
        with self._lock:
            return self._get_count

    def add_key(self, name, publish=True):
        # A unique kid per generated RSA key is essential; the fixture deliberately
        # never reuses identifiers when replacing a signing key.
        with self._lock:
            kid = 'ci-jwt-' + self._tag + '-' + name
            if kid in self._private:
                raise ValueError('duplicate signing key ID')
            self._private[kid] = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            if publish:
                self._published.append(kid)
            return kid

    def publish(self, kid):
        with self._lock:
            if kid not in self._private:
                raise ValueError('unknown signing key')
            if kid not in self._published:
                self._published.append(kid)

    def retire(self, kid):
        with self._lock:
            self._published.remove(kid)

    def available(self, value):
        with self._lock:
            self._available = bool(value)

    def _jwk(self, kid):
        numbers = self._private[kid].public_key().public_numbers()
        return {'kty': 'RSA', 'alg': 'RS256', 'use': 'sig', 'kid': kid,
                'n': b64(numbers.n.to_bytes((numbers.n.bit_length()+7)//8, 'big')),
                'e': b64(numbers.e.to_bytes((numbers.e.bit_length()+7)//8, 'big'))}

    def public_jwk(self, kid):
        with self._lock:
            if kid not in self._private:
                raise ValueError('unknown key')
            return dict(self._jwk(kid))

    def token(self, kid, issuer, audience, scope='activation:write', headers=None, **overrides):
        with self._lock:
            signing_key = self._private[kid]
        now = int(time.time())
        claims = {'iss': issuer, 'aud': [audience], 'sub': 'ci-verified-user',
                  'iat': now, 'nbf': now - 10, 'exp': now + 600, 'scope': scope}
        claims.update(overrides)
        header = {'alg': 'RS256', 'typ': 'JWT', 'kid': kid}
        if headers:
            header.update(headers)
        if header['alg'] not in ('RS256', 'RS512'):
            raise ValueError('fixture can only sign RSA JWTs')
        payload = b64(json.dumps(header, separators=(',', ':')).encode()) + '.' + \
            b64(json.dumps(claims, separators=(',', ':')).encode())
        return payload + '.' + b64(signing_key.sign(payload.encode(), padding.PKCS1v15(), hashes.SHA512() if header['alg']=='RS512' else hashes.SHA256()))

    def close(self):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=3)
