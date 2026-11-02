"""Actual loopback socket probe, with no false claim of remote hosts."""
import http.server
import json
import pathlib
import sys
import tempfile
import threading
import unittest
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from clock_probe import ClockHandler, probe, assemble

class ClockProbeTests(unittest.TestCase):
    def test_actual_loopback_three_probes(self):
        server=http.server.ThreadingHTTPServer(('127.0.0.1',0),ClockHandler)
        thread=threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            values=probe(f'http://127.0.0.1:{server.server_port}/clock',3)
            self.assertEqual(len(values),3)
            for item in values:
                self.assertLess(item['local_send_ns'],item['local_recv_ns'])
                self.assertLessEqual(item['remote_recv_ns'],item['remote_send_ns'])
        finally:
            server.shutdown();server.server_close();thread.join(timeout=3)
    def test_external_plaintext_denied(self):
        with self.assertRaisesRegex(ValueError,'loopback'):
            probe('http://example.test/clock',3)
    def test_wrong_clock_path_denied(self):
        with self.assertRaisesRegex(ValueError,'loopback'):
            probe('http://127.0.0.1:1/other',3)
    def test_collect_valid_reports(self):
        from test_clock_bounds import sample
        with tempfile.TemporaryDirectory() as d:
            paths=[]
            for i in range(2):
                p=pathlib.Path(d)/f'{i}.json'
                p.write_text(json.dumps({'schema_version':1,'run_id':'r','reference_id':'ref',
                    'shard_index':i,'samples':[sample()]*3}))
                paths.append(p)
            report,audit=assemble(paths,'r','ref',5)
            self.assertEqual(len(report['shards']),2)
            self.assertEqual(audit['status'],'BOUNDED_SELF_REPORTED_NOT_AUTHENTICATED')
    def test_collect_rejects_wrong_reference(self):
        from test_clock_bounds import sample
        with tempfile.TemporaryDirectory() as d:
            paths=[]
            for i in range(2):
                p=pathlib.Path(d)/f'{i}.json';p.write_text(json.dumps({'schema_version':1,'run_id':'r',
                    'reference_id':'ref' if i==0 else 'evil','shard_index':i,'samples':[sample()]*3}))
                paths.append(p)
            with self.assertRaisesRegex(ValueError,'identity mismatch'):
                assemble(paths,'r','ref',5)

if __name__=='__main__':unittest.main()
