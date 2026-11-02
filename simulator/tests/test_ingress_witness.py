"""Adversarial witness proxy contract and actual multi-process Java workload."""
import http.client
import http.server
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ingress_witness import WitnessState, WitnessServer, backend_url, MAX_BODY
from witness_audit import verify_witness
from distributed_merge import merge
from test_simulator import FakeService
from ingress_witness import WitnessHandler
from unittest import mock


def serve(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


def get_report(port):
    return json.load(urllib.request.urlopen(f'http://127.0.0.1:{port}/__witness/report', timeout=4))


class WitnessTests(unittest.TestCase):
    def setUp(self):
        self.service1 = FakeService(('127.0.0.1', 0))
        self.service2 = FakeService(('127.0.0.1', 0))
        self.service2.admissions = self.service1.admissions
        self.service2.lock = self.service1.lock
        self.t1 = serve(self.service1)
        self.t2 = serve(self.service2)
        self.run_id = 'witness-test-' + uuid.uuid4().hex[:10]
        self.state = WitnessState(self.run_id, [f'http://127.0.0.1:{s.server_port}' for s in (self.service1, self.service2)])
        self.gateway = WitnessServer(('127.0.0.1', 0), self.state)
        self.tw = serve(self.gateway)

    def tearDown(self):
        for s in (self.gateway, self.service1, self.service2):
            s.shutdown()
            s.server_close()

    @property
    def url(self):
        return f'http://127.0.0.1:{self.gateway.server_port}'

    def post(self, body=b'{}', token='demo-write-token'):
        req = urllib.request.Request(self.url + '/api/v1/requests', body,
            headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}, method='POST')
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def test_routing_and_no_sensitive_evidence(self):
        body = json.dumps({'externalId': 'id-001', 'customerId': 'sensitive', 'productCode': 'standard'}).encode()
        results = [self.post(body) for _ in range(6)]
        self.assertEqual([code for code, _ in results], [202, 200, 200, 200, 200, 200])
        report = get_report(self.gateway.server_port)
        self.assertEqual(report['finished'], 6)
        self.assertEqual(report['statuses'], {'202': 1, '200': 5})
        self.assertEqual([b['finished'] for b in report['backends']], [3, 3])
        self.assertNotIn('sensitive', json.dumps(report))
        self.assertNotIn('demo-write-token', json.dumps(report))
        self.assertEqual(report['inflight'], 0)
        self.assertEqual(sum(report['backend_latency_buckets'].values()), 6)
        self.assertEqual(report['client_delivery_failures'], 0)
        self.assertGreater(report['last_reply_ns'], report['first_forward_ns'])

    def test_reject_bad_paths_and_large_body_without_backend_effects(self):
        code, _ = self.post(b'x' * (MAX_BODY + 1))
        self.assertEqual(code, 413)
        req = urllib.request.Request(self.url + '/x', b'abc', method='POST')
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req)
        self.assertEqual(cm.exception.code, 404)
        self.assertEqual(self.service1.calls + self.service2.calls, 0)
        report = get_report(self.gateway.server_port)
        self.assertEqual(report['started'], 0)
        self.assertEqual(report['local_rejections'], 2)

    def test_missing_length_rejected(self):
        conn = http.client.HTTPConnection('127.0.0.1', self.gateway.server_port, timeout=3)
        conn.putrequest('POST', '/api/v1/requests')
        conn.endheaders()
        response = conn.getresponse()
        self.assertEqual(response.status, 400)
        response.read();conn.close()
        self.assertEqual(self.service1.calls, 0)

    def test_backpressure_is_explicit_and_uncounted(self):
        self.assertTrue(self.state.guard.acquire(False))
        # Occupy the other permits to cause deterministic pre-admission rejection.
        for _ in range(63):
            self.assertTrue(self.state.guard.acquire(False))
        try:
            self.assertEqual(self.post()[0], 503)
        finally:
            for _ in range(64):self.state.guard.release()
        self.assertEqual(get_report(self.gateway.server_port)['local_rejections'], 1)

    def test_transport_failure_cannot_masquerade_as_success(self):
        self.service1.shutdown();self.service1.server_close()
        self.service2.shutdown();self.service2.server_close()
        self.assertEqual(self.post()[0], 502)
        r = get_report(self.gateway.server_port)
        self.assertEqual(r['transport_errors'], 1)
        observed, errors = verify_witness(r, self.run_id, [{'status': 502}], {'502': 1})
        self.assertIsNone(observed)
        self.assertTrue(any('transport' in s for s in errors))

    def test_duplicate_content_length_rejected_before_forwarding(self):
        conn = http.client.HTTPConnection('127.0.0.1', self.gateway.server_port, timeout=3)
        conn.putrequest('POST', '/api/v1/requests')
        conn.putheader('Content-Length','2')
        conn.putheader('Content-Length','3')
        conn.endheaders()
        response=conn.getresponse()
        self.assertEqual(response.status,400)
        response.read();conn.close()
        self.assertEqual(self.service1.calls + self.service2.calls, 0)

    def test_chunked_transfer_not_accepted_as_trusted_forward(self):
        conn = http.client.HTTPConnection('127.0.0.1', self.gateway.server_port, timeout=3)
        conn.putrequest('POST', '/api/v1/requests')
        conn.putheader('Transfer-Encoding','chunked')
        conn.endheaders()
        response=conn.getresponse()
        self.assertEqual(response.status,400)
        response.read();conn.close()
        self.assertEqual(self.service1.calls + self.service2.calls, 0)

    def test_client_delivery_failure_is_distinct_from_upstream_success(self):
        state=WitnessState('sample', ['http://127.0.0.1:1'])
        ticket=state.begin();state.end(0,202,False,ticket)
        state.delivery_failed()
        report=state.report()
        self.assertEqual(report['statuses'], {'202':1})
        self.assertEqual(report['client_delivery_failures'],1)
        observed,errors=verify_witness(report,'sample',[{'status':202}],{'202':1})
        self.assertIsNone(observed)
        self.assertTrue(any('delivery' in e for e in errors))

    def test_a_snapshot_cannot_look_quiescent_before_client_delivery_finishes(self):
        waiting = threading.Event()
        release = threading.Event()
        original = WitnessHandler.respond
        def held_response(handler, code, body=b'', content_type='application/json'):
            if handler.path == '/api/v1/requests':
                waiting.set()
                if not release.wait(8):
                    raise TimeoutError('test response hold unexpectedly timed out')
            return original(handler,code,body,content_type)
        payload=json.dumps({'externalId':'blocking-snapshot', 'customerId':'secret','productCode':'standard'}).encode()
        result=[]
        try:
            with mock.patch.object(WitnessHandler,'respond',held_response):
                t=threading.Thread(target=lambda:result.append(self.post(payload)),daemon=True)
                t.start()
                self.assertTrue(waiting.wait(4),'response reached client delivery boundary')
                snapshot=self.state.report()
                self.assertEqual(snapshot['started'],1)
                self.assertEqual(snapshot['inflight'],1)
                self.assertEqual(snapshot['finished'],0)
                release.set()
                t.join(timeout=5)
                self.assertFalse(t.is_alive())
                self.assertEqual([r[0] for r in result],[202])
        finally:
            release.set()
        snapshot=self.state.report()
        self.assertEqual(snapshot['finished'],1)
        self.assertEqual(snapshot['inflight'],0)
        self.assertEqual(snapshot['client_delivery_failures'],0)

    def test_real_two_coordinators_four_java_processes(self):
        subprocess.run([str(ROOT/'build.sh')], check=True, stdout=subprocess.DEVNULL)
        with tempfile.TemporaryDirectory(prefix='deroyque-witness-') as tmp:
            run_id = self.run_id
            start = int(time.time() * 1000) + 13_000
            env = {**os.environ, 'DEROYQUE_SIM_WRITE_TOKEN': 'demo-write-token'}
            processes = []
            for shard in range(2):
                cmd = [sys.executable, str(ROOT/'run.py'), '--url', self.url, '--jvms=2',
                       '--concurrency=6', '--requests=64', '--keys=16', '--rate=60',
                       '--conflict-percent=40', '--shard-count=2', f'--shard-index={shard}',
                       f'--start-at-ms={start}', f'--run-id={run_id}',
                       '--output-dir', str(Path(tmp)/f'shard-{shard}')]
                processes.append(subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE,
                                                  stderr=subprocess.PIPE, text=True))
            for process in processes:
                try:
                    out, err = process.communicate(timeout=65)
                except subprocess.TimeoutExpired:
                    process.kill();process.communicate()
                    self.fail('Producer coordinator hung')
                self.assertEqual(process.returncode, 0, out[-1600:]+err[-1600:])
            paths = [Path(tmp)/f'shard-{i}' for i in range(2)]
            report = get_report(self.gateway.server_port)
            self.assertEqual(report['finished'], 64)
            self.assertEqual(len(report['backends']), 2)
            self.assertTrue(all(x['finished'] > 0 for x in report['backends']))
            result = merge(paths, gateway_witness=report)
            self.assertEqual(result['outcome'], 'PASS', result)
            self.assertEqual(result['total_producer_jvms'], 4)
            self.assertEqual(result['global_recorded_posts'], 64)
            self.assertIsNotNone(result['global_throughput_qps'])
            self.assertEqual(result['gateway_witness']['total_http_responses'], 64)
            self.assertEqual(result['host_evidence']['status'], 'SAME_KERNEL_OBSERVED')
            self.assertIn('gateway', result['measurement_note'].lower())
            # False witness statuses, omissions and fabricated intervals must fail.
            for mutate in ('omit', 'status', 'clock'):
                forged = json.loads(json.dumps(report))
                if mutate == 'omit': forged['finished'] -= 1
                if mutate == 'status': forged['statuses']['202'] += 1
                if mutate == 'clock': forged['last_reply_ns'] = forged['first_forward_ns']
                self.assertEqual(merge(paths, gateway_witness=forged)['outcome'], 'FAIL', mutate)
            print('WITNESS_REAL_JVMS_PASS coordinators=2 producer_jvms=4 backends=2 requests=64')


class WitnessAuditTests(unittest.TestCase):
    def fixture(self):
        r = WitnessState('audit', ['http://127.0.0.1:1', 'http://127.0.0.1:2'])
        for index, code in ((0, 202), (1, 200), (0, 409)):
            ticket=r.begin();r.end(index, code, False, ticket)
        return r.report()

    def test_cross_checks_and_false_claim_denial(self):
        r = self.fixture()
        view, errors = verify_witness(r, 'audit', [1, 2, 3], {'202':1, '200':1, '409':1})
        self.assertFalse(errors)
        self.assertGreater(view['gateway_completed_post_qps'], 0)
        self.assertEqual(view['backend_count'], 2)
        for field, val in (('started', True), ('inflight', 1), ('local_rejections', 1), ('client_delivery_failures', 1), ('run_id', 'wrong'),
                           ('scope', 'per_jvm'), ('peak_inflight', -1)):
            bad = json.loads(json.dumps(r));bad[field] = val
            self.assertIsNone(verify_witness(bad, 'audit', [1,2,3], {'202':1,'200':1,'409':1})[0], field)
        bad_histogram = json.loads(json.dumps(r));bad_histogram['backend_latency_buckets'].clear()
        self.assertIsNone(verify_witness(bad_histogram, 'audit', [1,2,3], {'202':1,'200':1,'409':1})[0])
        missing = json.loads(json.dumps(r));missing['backends'][1]['finished'] = 0
        self.assertIsNone(verify_witness(missing, 'audit', [1,2,3], {'202':1,'200':1,'409':1})[0])

    def test_validate_loopback_only_and_identity(self):
        for bad in ('https://127.0.0.1:1', 'http://example.com:1', 'http://127.0.0.1:1/x',
                    'http://user:pass@127.0.0.1:1', 'http://127.0.0.1:1?x=y'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):backend_url(bad)
        with self.assertRaises(ValueError):WitnessState('../bad', ['http://127.0.0.1:1234'])
        with self.assertRaises(ValueError):WitnessState('valid', ['http://127.0.0.1:1']*2)
        with self.assertRaises(ValueError):WitnessServer(('0.0.0.0',0),WitnessState('valid', ['http://127.0.0.1:1']))


if __name__ == '__main__':
    unittest.main(verbosity=2)
