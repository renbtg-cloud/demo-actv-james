"""Mutation controls: equality of aggregate counts must not mask wrong identities."""
import sqlite3
import sys
import tempfile
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parent))
from gateway_identity_audit import (expected_keys,postgres_identity_query,
                                    audit_postgres_rows,audit_sqlite_effects)

RUN='gw-abcdef1200'
RID1='11111111-1111-4111-8111-111111111111'
RID2='22222222-2222-4222-8222-222222222222'
GOOD=f'{RUN}-0|{RID1}|1|1|1|0|1\n{RUN}-1|{RID2}|1|1|1|0|1'

class IdentityProofTest(unittest.TestCase):
    def test_positive_postgres(self):
        self.assertEqual(audit_postgres_rows(GOOD,RUN,2)['verified_signed_attestations'],2)

    def test_missing_one_key_but_aggregate_count_unchanged(self):
        changed=GOOD.replace(f'{RUN}-1',f'{RUN}-9')
        with self.assertRaises(ValueError):audit_postgres_rows(changed,RUN,2)

    def test_duplicate_identity_with_same_number_of_rows(self):
        changed=GOOD.replace(f'{RUN}-1',f'{RUN}-0')
        with self.assertRaises(ValueError):audit_postgres_rows(changed,RUN,2)

    def test_offsetting_mismatched_graph_counts(self):
        changed=GOOD.replace('|1|1|1|0|1','|2|0|1|0|1',1)
        with self.assertRaises(ValueError):audit_postgres_rows(changed,RUN,2)

    def test_cross_request_settlement_detected(self):
        changed=GOOD.replace('|1|1|1|0|1','|1|1|1|1|1',1)
        with self.assertRaises(ValueError):audit_postgres_rows(changed,RUN,2)

    def test_invalid_attestation_attributes_detected(self):
        changed=GOOD.replace('|1|1|1|0|1','|1|1|1|0|0',1)
        with self.assertRaises(ValueError):audit_postgres_rows(changed,RUN,2)

    def test_nonsense_or_missing_pg_rows(self):
        for data in ['', GOOD.splitlines()[0], GOOD+'\ninvalid']:
            with self.subTest(data=data), self.assertRaises(ValueError):
                audit_postgres_rows(data,RUN,2)

    def test_pg_query_uses_read_only_correlated_cardinality(self):
        query=postgres_identity_query('gw_abcd',RUN)
        self.assertIn('a.did=d.did',query)
        self.assertIn('AND d.rid<>r.rid',query)
        self.assertIn('reported_outcome',query)
        self.assertNotIn('INSERT ',query)
        self.assertNotIn('DELETE ',query)

    def test_reject_untrusted_identifiers(self):
        for unsafe in ['x; DROP TABLE a','x%','x\'']:
            with self.subTest(unsafe=unsafe),self.assertRaises(ValueError):
                expected_keys(unsafe,2)
        for unsafe in ['a; DROP', 'UPPER','']:
            with self.subTest(unsafe=unsafe),self.assertRaises(ValueError):
                postgres_identity_query(unsafe,RUN)

    def fixture(self, rows):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p=Path(self.tmp.name)/'provider.db'
        with sqlite3.connect(p) as conn:
            conn.execute('CREATE TABLE effect(key TEXT,generation INTEGER,outcome TEXT)')
            conn.executemany('INSERT INTO effect VALUES(?,?,?)',rows)
        return p

    def test_positive_sqlite(self):
        p=self.fixture([(f'{RUN}-{i}',1,'ACTIVATED') for i in range(2)])
        self.assertEqual(audit_sqlite_effects(p,RUN,2)['keys'],2)

    def test_sqlite_same_total_wrong_key(self):
        p=self.fixture([(f'{RUN}-0',1,'ACTIVATED'),(f'{RUN}-9',1,'ACTIVATED')])
        with self.assertRaises(ValueError):audit_sqlite_effects(p,RUN,2)

    def test_sqlite_generation_duplication_with_same_total(self):
        p=self.fixture([(f'{RUN}-0',1,'ACTIVATED'),(f'{RUN}-0',2,'ACTIVATED')])
        with self.assertRaises(ValueError):audit_sqlite_effects(p,RUN,2)

    def test_sqlite_wrong_outcome(self):
        p=self.fixture([(f'{RUN}-0',1,'REJECTED'),(f'{RUN}-1',1,'ACTIVATED')])
        with self.assertRaises(ValueError):audit_sqlite_effects(p,RUN,2)

    def test_sqlite_read_only(self):
        p=self.fixture([(f'{RUN}-0',1,'ACTIVATED'),(f'{RUN}-1',1,'ACTIVATED')])
        audit_sqlite_effects(p,RUN,2)
        with sqlite3.connect(p) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM effect').fetchone()[0],2)

    def test_sqlite_missing_store(self):
        with self.assertRaises(ValueError):audit_sqlite_effects('/no/provider.sqlite',RUN,2)

if __name__=='__main__':unittest.main()
