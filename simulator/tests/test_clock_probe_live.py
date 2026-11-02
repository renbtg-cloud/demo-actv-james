"""Real loopback clock reference and independently forked probe clients.

This proves local transport and collector behavior, never remote host identity.
"""
import json
import pathlib
import socket
import subprocess
import sys
import tempfile
import time
import unittest

SIM = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SIM))
from clock_probe import assemble, probe


class LiveClockProbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='clock-live-')
        self.addCleanup(self.tmp.cleanup)
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            port = s.getsockname()[1]
        self.url = f'http://127.0.0.1:{port}/clock'
        self.proc = subprocess.Popen([sys.executable, str(SIM/'clock_probe.py'), 'serve',
                                      '--port', str(port)], stdout=subprocess.DEVNULL,
                                     stderr=subprocess.PIPE)
        self.addCleanup(self._close)
        for _ in range(120):
            if self.proc.poll() is not None:
                self.fail('clock reference exited before readiness')
            try:
                with socket.create_connection(('127.0.0.1',port), timeout=0.1):
                    break
            except OSError:
                time.sleep(0.05)
        else:
            self.fail('clock reference did not become ready')

    def _close(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=3)
        if self.proc.stderr:
            self.proc.stderr.close()

    def _command(self, shard, path):
        return [sys.executable, str(SIM/'clock_probe.py'), 'probe', '--url',self.url,
                '--run-id','real-clock-live', '--reference-id','local-lab',
                '--shard-index',str(shard), '--samples','8', '--output',str(path)]

    def test_two_real_processes_assemble_out_of_order_and_reject_mutation(self):
        d = pathlib.Path(self.tmp.name)
        outputs = [d/'host-0.json',d/'host-1.json']
        children = [subprocess.Popen(self._command(i, outputs[i]),
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    for i in range(2)]
        for child in children:
            out, err = child.communicate(timeout=12)
            self.assertEqual(child.returncode,0,(out+err).decode(errors='replace'))
        combined, result = assemble(outputs[::-1], 'real-clock-live', 'local-lab')
        self.assertEqual([x['shard_index'] for x in combined['shards']],[0,1])
        self.assertEqual(result['status'],'BOUNDED_SELF_REPORTED_NOT_AUTHENTICATED')
        self.assertEqual(len(result['per_shard']),2)
        self.assertTrue(all(x['probe_count']==8 and 0<=x['uncertainty_width_ns']<=100_000_000
                            for x in result['per_shard']))
        modified=json.loads(outputs[0].read_text())
        modified['samples'][0]['remote_recv_ns'] += 5_000_000_000
        modified['samples'][0]['remote_send_ns'] += 5_000_000_000
        outputs[0].write_text(json.dumps(modified))
        with self.assertRaisesRegex(ValueError,'Clock report failed'):
            assemble(outputs[::-1], 'real-clock-live', 'local-lab')

    def test_reference_outage_does_not_create_false_evidence(self):
        self._close()
        output = pathlib.Path(self.tmp.name)/'unavailable.json'
        child = subprocess.run(self._command(0,output),capture_output=True,timeout=5)
        self.assertNotEqual(child.returncode,0)
        self.assertFalse(output.exists())

    def test_reject_nonloopback_remote_reference_before_connection(self):
        with self.assertRaises(ValueError):
            probe('http://192.0.2.123/clock',repeats=3)


if __name__=='__main__': unittest.main()
