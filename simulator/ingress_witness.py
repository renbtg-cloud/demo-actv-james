#!/usr/bin/env python3
"""Opt-in *laboratory* HTTP ingress witness, using one monotonic clock.

The witness proxies complete HTTP requests to independent backend listener ports.
It captures counts and timings, never credentials or payloads. It is deliberately
loopback-only: use a separate authenticated TLS tunnel for remote experiments.
It measures gateway response rate, NOT provider effects or backend-only capacity.
"""
import argparse
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

MAX_BODY = 64 * 1024
MAX_REPLY = 512 * 1024
RUN_RE = re.compile(r'[A-Za-z0-9_-]{1,48}\Z')
PATH_RE = re.compile(r'/api/v1/requests/[A-Za-z0-9_-]{1,100}\Z')


def backend_url(value):
    u = urllib.parse.urlsplit(value)
    if (u.scheme != 'http' or u.hostname not in ('127.0.0.1', 'localhost', '::1')
            or not u.port or u.username or u.password or u.query or u.fragment
            or u.path not in ('', '/')):
        raise ValueError('Witness backends must be plain loopback HTTP base URLs; use an authenticated local tunnel')
    return value.rstrip('/')


class WitnessState:
    def __init__(self, run_id, backends, max_inflight=64):
        if not RUN_RE.fullmatch(run_id):
            raise ValueError('Invalid witness run ID')
        if not 1 <= len(backends) <= 16 or len(set(backends)) != len(backends):
            raise ValueError('Require 1..16 unique backend listeners')
        if not 1 <= max_inflight <= 256:
            raise ValueError('max_inflight must be 1..256')
        self.run_id = run_id
        self.backends = [backend_url(b) for b in backends]
        self.guard = threading.BoundedSemaphore(max_inflight)
        self.lock = threading.Lock()
        self.position = 0
        self.started = 0
        self.finished = 0
        self.inflight = 0
        self.peak_inflight = 0
        self.first_forward_ns = 0
        self.last_reply_ns = 0
        self.statuses = Counter()
        self.by_backend = [Counter() for _ in backends]
        self.transport_errors = 0
        self.local_rejections = 0

    def choose(self):
        with self.lock:
            index = self.position % len(self.backends)
            self.position += 1
            return index, self.backends[index]

    def begin(self):
        start = time.monotonic_ns()
        with self.lock:
            self.started += 1
            self.inflight += 1
            self.peak_inflight = max(self.peak_inflight, self.inflight)
            if self.first_forward_ns == 0:
                self.first_forward_ns = start
        return start

    def end(self, backend_index, status, transport_error):
        end = time.monotonic_ns()
        with self.lock:
            self.finished += 1
            self.inflight -= 1
            self.last_reply_ns = max(self.last_reply_ns, end)
            self.statuses[str(status)] += 1
            self.by_backend[backend_index][str(status)] += 1
            self.transport_errors += int(transport_error)

    def rejected(self):
        with self.lock:
            self.local_rejections += 1

    def report(self):
        with self.lock:
            return {'schema_version': 1, 'run_id': self.run_id,
                'scope': 'single_loopback_gateway_monotonic_response_clock',
                'backends': [{'index': i, 'url': url, 'finished': sum(c.values()),
                              'statuses': dict(c)} for i, (url, c) in enumerate(zip(self.backends, self.by_backend))],
                'started': self.started, 'finished': self.finished,
                'inflight': self.inflight, 'peak_inflight': self.peak_inflight,
                'first_forward_ns': self.first_forward_ns, 'last_reply_ns': self.last_reply_ns,
                'statuses': dict(self.statuses), 'transport_errors': self.transport_errors,
                'local_rejections': self.local_rejections,
                'measurement_note': 'Single gateway monotonic window; captures returned HTTP responses, including transport-generated 502; NOT provider completions, hardware isolation, or unproxied service capacity.'}


class WitnessServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 256
    allow_reuse_address = True
    def __init__(self, address, state, timeout=10):
        if address[0] != '127.0.0.1':
            raise ValueError('Witness only binds IPv4 loopback')
        super().__init__(address, WitnessHandler)
        self.state = state
        self.backend_timeout = timeout


class WitnessHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def log_message(self, *args):
        # Default BaseHTTPRequestHandler access logs may contain attacker-controlled
        # URLs. Never log Authorization, request bodies or backend responses.
        pass

    def respond(self, code, body=b'', content_type='application/json'):
        self.send_response(code)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Content-Type', content_type)
        self.send_header('Connection', 'close')
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass
        self.close_connection = True

    def do_GET(self):
        if self.path == '/__witness/health':
            return self.respond(200, b'{"status":"ok"}')
        if self.path == '/__witness/report':
            report = json.dumps(self.server.state.report(), sort_keys=True).encode()
            return self.respond(200, report)
        if PATH_RE.fullmatch(self.path) or self.path == '/api/v1/health/readiness':
            return self.forward('GET', b'', measured=False)
        self.server.state.rejected()
        return self.respond(404, b'{"error":"NOT_FOUND"}')

    def do_POST(self):
        if self.path != '/api/v1/requests':
            self.server.state.rejected()
            return self.respond(404, b'{"error":"NOT_FOUND"}')
        if 'Transfer-Encoding' in self.headers or self.headers.get_all('Content-Length', []) == []:
            self.server.state.rejected()
            return self.respond(400, b'{"error":"CONTENT_LENGTH_REQUIRED"}')
        lengths = self.headers.get_all('Content-Length', [])
        if len(lengths) != 1 or not re.fullmatch(r'[0-9]{1,9}', lengths[0]):
            self.server.state.rejected()
            return self.respond(400, b'{"error":"INVALID_LENGTH"}')
        length = int(lengths[0])
        if length > MAX_BODY:
            self.server.state.rejected()
            return self.respond(413, b'{"error":"BODY_TOO_LARGE"}')
        body = self.rfile.read(length)
        if len(body) != length:
            self.server.state.rejected()
            return self.respond(400, b'{"error":"TRUNCATED_BODY"}')
        return self.forward('POST', body, measured=True)

    def forward(self, method, body, measured):
        state = self.server.state
        if not state.guard.acquire(blocking=False):
            state.rejected()
            return self.respond(503, b'{"error":"WITNESS_OVERLOADED"}')
        index, backend = state.choose()
        started = False
        status = 502
        failed = False
        answer = b'{"error":"WITNESS_UPSTREAM_UNAVAILABLE"}'
        reply_type = 'application/json'
        try:
            if measured:
                state.begin()
                started = True
            headers = {'Accept': 'application/json'}
            # No credentials or client-supplied forwarding headers in evidence.
            for field in ('Authorization', 'Content-Type', 'X-Deroyque-Load-Run'):
                if field in self.headers:
                    headers[field] = self.headers[field]
            request = urllib.request.Request(backend + self.path, data=body if method == 'POST' else None,
                                             headers=headers, method=method)
            try:
                upstream = urllib.request.urlopen(request, timeout=self.server.backend_timeout)
            except urllib.error.HTTPError as error:
                upstream = error
            with upstream:
                status = upstream.status
                answer = upstream.read(MAX_REPLY + 1)
                if len(answer) > MAX_REPLY:
                    raise ValueError('oversize backend reply')
                reply_type = upstream.headers.get('Content-Type', 'application/json')
                # Never propagate arbitrary upstream header content into a response.
                if reply_type not in ('application/json', 'application/json;charset=UTF-8', 'text/plain'):
                    reply_type = 'application/json'
        except (OSError, ValueError, urllib.error.URLError):
            status = 502
            failed = True
            answer = b'{"error":"WITNESS_UPSTREAM_UNAVAILABLE"}'
            reply_type = 'application/json'
        finally:
            if started:
                state.end(index, status, failed)
            state.guard.release()
        self.respond(status, answer, reply_type)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--backend', action='append', required=True, help='Repeat for each distinct loopback service port')
    parser.add_argument('--port', type=int, default=18088)
    parser.add_argument('--backend-timeout', type=float, default=10)
    parser.add_argument('--max-inflight', type=int, default=64)
    args = parser.parse_args(argv)
    try:
        if not 0 <= args.port <= 65535 or not 0.5 <= args.backend_timeout <= 60:
            raise ValueError('Invalid port or backend timeout')
        state = WitnessState(args.run_id, args.backend, args.max_inflight)
        with WitnessServer(('127.0.0.1', args.port), state, args.backend_timeout) as srv:
            print(f'INGRESS_WITNESS_READY port={srv.server_port} backends={len(state.backends)}', flush=True)
            srv.serve_forever(poll_interval=0.1)
    except (OSError, ValueError) as exc:
        print(f'INGRESS_WITNESS_ERROR {type(exc).__name__}: {exc}', flush=True)
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
