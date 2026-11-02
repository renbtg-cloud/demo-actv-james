#!/usr/bin/env python3
"""Regression suite for the independent simulator: HTTP contract + concurrency + safety."""
import http.server
import importlib.util
import json
import os
import pathlib
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock
import uuid

ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import run as simulator

class FakeService(http.server.ThreadingHTTPServer):
    request_queue_size=256 # the fake server must not drop load due to its default backlog of five
    daemon_threads=True
    allow_reuse_address=True
    def __init__(self,addr):
        super().__init__(addr, Handler)
        self.lock=threading.RLock()
        self.admissions={}
        self.calls=0

class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*args):pass
    def respond(self,status,data):
        body=json.dumps(data,separators=(',',':')).encode()
        self.send_response(status)
        self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(body)))
        self.end_headers();self.wfile.write(body)
    def do_POST(self):
        if self.path!='/api/v1/requests':return self.respond(404,{'error':'NOT_FOUND'})
        if self.headers.get('Authorization')!='Bearer demo-write-token':return self.respond(401,{'error':'UNAUTHORIZED'})
        try:d=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        except Exception:return self.respond(400,{'error':'INVALID_JSON'})
        with self.server.lock:
            self.server.calls+=1
            key=d['externalId']
            v=self.server.admissions.get(key)
            if not v:
                v=(str(uuid.uuid4()),d)
                self.server.admissions[key]=v
                code=202
            else:code=200 if d==v[1] else 409
        return self.respond(code,{'id':v[0],'idempotentReplay':code==200} if code!=409 else {'error':'EXTERNAL_ID_CONFLICT'})
    def do_GET(self):
        if not self.path.startswith('/api/v1/requests/'):return self.respond(404,{})
        if self.headers.get('Authorization')!='Bearer demo-read-token':return self.respond(401,{})
        target=self.path.rsplit('/',1)[-1]
        with self.server.lock:
            row=next(((id,d) for id,d in self.server.admissions.values() if id==target),None)
        if row is None:return self.respond(404,{})
        return self.respond(200,{'id':row[0],**row[1],'status':'DONE','attempts':1})

class Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        subprocess.run([str(ROOT/'build.sh')],check=True,stdout=subprocess.DEVNULL)
        cls.service=FakeService(('127.0.0.1',0))
        cls.thread=threading.Thread(target=cls.service.serve_forever,daemon=True);cls.thread.start()
    @classmethod
    def tearDownClass(cls):cls.service.shutdown();cls.service.server_close()
    def test_01_multi_jvm_idempotency_conflict_verify(self):
        with tempfile.TemporaryDirectory(prefix='deroyque-sim-test-') as temp:
            env={**os.environ,'DEROYQUE_SIM_WRITE_TOKEN':'demo-write-token','DEROYQUE_SIM_READ_TOKEN':'demo-read-token'}
            cmd=[sys.executable,str(ROOT/'run.py'),'--url',f'http://127.0.0.1:{self.service.server_port}',
                 '--jvms=4','--concurrency=12','--requests=120','--keys=30','--conflict-percent=40',
                 '--start-delay-ms=2000','--verify-state','--output-dir',str(pathlib.Path(temp)/'run')]
            p=subprocess.run(cmd,env=env,text=True,capture_output=True,timeout=90)
            self.assertEqual(p.returncode,0,p.stdout[-3000:]+p.stderr)
            result=json.loads((pathlib.Path(temp)/'run'/'summary.json').read_text())
            self.assertEqual(result['observed']['records'],120)
            self.assertEqual(result['observed']['admitted_keys_observed'],30)
            self.assertEqual(result['observed']['http_statuses']['202'],30)
            self.assertGreater(int(result['observed']['http_statuses'].get('409',0)),0)
            self.assertEqual(result['violations'],[])
            self.assertEqual(result['state_verification_errors'],[])
            self.assertEqual(result['outcome'],'PASS')
    def test_02_wrong_token_is_reported_failure(self):
        with tempfile.TemporaryDirectory(prefix='deroyque-sim-bad-token-') as temp:
            env={**os.environ,'DEROYQUE_SIM_WRITE_TOKEN':'wrong-secret'}
            cmd=[sys.executable,str(ROOT/'run.py'),'--url',f'http://127.0.0.1:{self.service.server_port}',
                 '--jvms=2','--requests=8','--keys=8','--start-delay-ms=1000','--output-dir',str(pathlib.Path(temp)/'run')]
            p=subprocess.run(cmd,env=env,text=True,capture_output=True,timeout=40)
            self.assertEqual(p.returncode,1,p.stdout[-1000:]+p.stderr)
            result=json.loads((pathlib.Path(temp)/'run'/'summary.json').read_text())
            self.assertEqual(result['observed']['http_statuses']['401'],8)
            self.assertEqual(result['outcome'],'FAIL')
    def test_03_remote_requires_opt_in(self):
        args=simulator.parse_args(['--url=http://example.invalid:9999','--requests=1','--jvms=1'])
        with self.assertRaisesRegex(ValueError,'--allow-remote'):simulator.validate_target(args)
    def test_04_multi_jvm_auditor_catches_double_accept(self):
        rows=[{'sequence':0,'slot':0,'variant':0,'status':202,'id':'one','latency_ns':1},
              {'sequence':1,'slot':0,'variant':1,'status':202,'id':'two','latency_ns':1}]
        violations,*_=simulator.audit(rows,1)
        self.assertTrue(any('TWO ACCEPTED PAYLOADS' in v for v in violations))
        self.assertTrue(any('TWO UUIDs' in v for v in violations))
    def test_05_percentiles_are_nearest_rank(self):
        self.assertEqual(simulator.duration_ms_to_percentile([1000000,2000000,3000000,4000000],95),4.0)
    def test_06_replayed_without_creation_is_a_violation(self):
        rows=[{'sequence':0,'slot':0,'variant':0,'status':200,'id':'one','latency_ns':1}]
        violations,*_=simulator.audit(rows,1)
        self.assertTrue(any('replay without exactly one creation' in v for v in violations))
    def test_07_verification_counts_each_key_once_after_polling(self):
        observations=[]
        def state_sequence(url,token,uid,timeout_ms):
            observations.append(uid)
            status='READY' if len(observations)==1 else 'DONE'
            return 200,{'id':'uuid-0','externalId':'testrun-0','customerId':'c-0','productCode':'standard','status':status}
        with mock.patch.object(simulator,'get_status',side_effect=state_sequence):
            failures,states=simulator.verify('http://127.0.0.1:1','read-token','testrun',{0:0},{0:'uuid-0'},1000,1.0)
        self.assertFalse(failures)
        self.assertEqual(states,{'DONE':1})
        self.assertEqual(len(observations),2)

if __name__=='__main__':unittest.main(verbosity=2)
