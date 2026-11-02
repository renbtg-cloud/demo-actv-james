"""Independent collector processes must not consume the same run twice."""
import concurrent.futures
import json
import pathlib
import sqlite3
import subprocess
import sys
import tempfile
import unittest

SIM=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(SIM))
from host_session import digest

class CollectorLedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='collector-ledger-')
        self.addCleanup(self.tmp.cleanup)
        self.root=pathlib.Path(self.tmp.name)
        self.session={'schema_version':1,'run_id':'one-run','nonce':'a'*64,
                      'manifest_sha256':'b'*64,'pins_sha256':'c'*64,
                      'host_assignments':['host-a','host-b']}
        self.session_file=self.root/'session.json'
        self.session_file.write_text(json.dumps(self.session))
        self.report_file=self.root/'verified.json'
        self.report_file.write_text(json.dumps({'run_id':'one-run','outcome':'PASS',
          'session_sha256':digest(self.session),
          'signed_host_evidence':{'status':'ALL_PINNED_SIGNING_KEYS_VERIFIED_NOT_HOST_ATTESTATION'}}))
        self.ledger=self.root/'consumed.db'

    def cmd(self):
        return [sys.executable,str(SIM/'collector_ledger.py'),'--ledger',str(self.ledger),
          '--session',str(self.session_file),'--verified-merge',str(self.report_file)]

    def test_four_genuine_python_processes_one_winner(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            jobs=[pool.submit(subprocess.run,self.cmd(),capture_output=True,text=True,timeout=20)
                  for _ in range(4)]
            outputs=[j.result() for j in jobs]
        self.assertEqual(sum(p.returncode==0 for p in outputs),1,[p.stderr for p in outputs])
        self.assertEqual(sum(p.returncode==2 for p in outputs),3)
        with sqlite3.connect(self.ledger) as db:
            rows=db.execute('SELECT session_sha256,run_id FROM used_session').fetchall()
        self.assertEqual(rows,[(digest(self.session),'one-run')])

    def test_replay_of_same_id_but_changed_session_rejected(self):
        ok=subprocess.run(self.cmd(),capture_output=True,text=True,timeout=10)
        self.assertEqual(ok.returncode,0,ok.stderr)
        changed=dict(self.session,nonce='d'*64)
        self.session_file.write_text(json.dumps(changed))
        document=json.loads(self.report_file.read_text())
        document['session_sha256']=digest(changed)
        self.report_file.write_text(json.dumps(document))
        replay=subprocess.run(self.cmd(),capture_output=True,text=True,timeout=10)
        self.assertEqual(replay.returncode,2)
        self.assertIn('replay',replay.stderr)

    def test_fail_result_must_not_consume_run(self):
        document=json.loads(self.report_file.read_text())
        document['outcome']='FAIL'
        self.report_file.write_text(json.dumps(document))
        result=subprocess.run(self.cmd(),capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,2)
        self.assertFalse(self.ledger.exists())

    def test_symlink_db_rejected(self):
        original=self.root/'other.db';original.write_bytes(b'do-not-touch')
        self.ledger.symlink_to(original)
        result=subprocess.run(self.cmd(),capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,2)
        self.assertEqual(original.read_bytes(),b'do-not-touch')

if __name__=='__main__':unittest.main()
