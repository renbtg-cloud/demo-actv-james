"""Adversarial proof-chain mutations: passing totals alone is not enough."""
import sqlite3
from contextlib import closing
import sys
import tempfile
from pathlib import Path
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parent))
from gateway_lineage_audit import (
    postgres_lineage_query, audit_lineage_rows, schema_cardinality_query,
    audit_schema_cardinality, audit_sqlite_index_and_proof_times)

RUN='gw-proof789'
RIDS=['11111111-1111-4111-8111-111111111111','22222222-2222-4222-8222-222222222222']
GOOD='\n'.join(f'{RUN}-{i}|{rid}|DONE|1|1|0' for i,rid in enumerate(RIDS))
STAMP=1793640000000

class LineageAudits(unittest.TestCase):
    def test_valid_terminal_signed_graph(self):
        self.assertEqual(audit_lineage_rows(GOOD,RUN,2)['verified_terminal_lineages'],2)

    def test_all_lineage_corruptions_fail(self):
        mutations={
            'not-terminal':GOOD.replace('|DONE|','|WAITING|',1),
            'wrong-settlement':GOOD.replace('|1|1|0','|0|1|0',1),
            'duplicate-signed-observation':GOOD.replace('|1|1|0','|1|2|0',1),
            'unlinked-observation':GOOD.replace('|1|1|0','|1|1|1',1),
            'same-count-wrong-identity':GOOD.replace(f'{RUN}-1',f'{RUN}-9'),
            'duplicate-key':GOOD.replace(f'{RUN}-1',f'{RUN}-0'),
            'missing':GOOD.splitlines()[0],
            'empty':'',
            'malformed':GOOD+'\nunknown',
        }
        for label,data in mutations.items():
            with self.subTest(label=label),self.assertRaises(ValueError):
                audit_lineage_rows(data,RUN,2)

    def test_lineage_sql_inspects_observation_and_settlement(self):
        query=postgres_lineage_query('gw_proof',RUN)
        for fragment in ("o.did=d.did","o.source='SIGNED_PROVIDER_LOOKUP'", "s.target='DONE'",
                         'a.oid=o.oid','o.http_status=200'):
            self.assertIn(fragment,query)
        for unsafe in ('gw%','gw; DROP','ABC'):
            with self.assertRaises(ValueError):postgres_lineage_query(unsafe,RUN)

    def test_schema_wide_counts_detect_offsetting_and_orphans(self):
        self.assertEqual(audit_schema_cardinality('2|2|2|2|0|0',2)['clean_schema_keys'],2)
        for data in ('2|3|2|2|0|0','3|2|2|2|0|0','2|2|1|3|0|0',
                     '2|2|2|2|1|0','2|2|2|2|0|1','2|2|2|2|0|0\nextra',
                     '2|2|2|2|0|NaN','2|2|2|2|0|0|0'):
            with self.subTest(data=data),self.assertRaises(ValueError):
                audit_schema_cardinality(data,2)
        q=schema_cardinality_query('gw_proof',RUN)
        self.assertIn('p20_rejected_attestation',q)
        self.assertIn('p14_evidence_incident',q)

    def _database(self,mutate=None):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        p=Path(temp.name)/'provider.sqlite'
        with closing(sqlite3.connect(p)) as db:
            db.execute('CREATE TABLE effect(key TEXT,generation INTEGER,outcome TEXT,created_ms INTEGER)')
            db.execute('CREATE TABLE idem_index(key TEXT,generation INTEGER,expires_ms INTEGER)')
            db.executemany('INSERT INTO effect VALUES(?,?,?,?)',
                           [(f'{RUN}-{i}',1,'ACTIVATED',STAMP-1000) for i in range(2)])
            db.executemany('INSERT INTO idem_index VALUES(?,?,?)',
                           [(f'{RUN}-{i}',1,STAMP+600000) for i in range(2)])
            if mutate:mutate(db)
            db.commit()
        return p

    @staticmethod
    def _signed():
        return '\n'.join(f'{RUN}-{i}|valid-placeholder-token|nonce-{i}|{STAMP}' for i in range(2))

    def test_valid_sqlite_index_and_signed_time_window(self):
        p=self._database()
        result=audit_sqlite_index_and_proof_times(p,self._signed(),RUN,2)
        self.assertEqual(result['indexed_effect_proofs'],2)

    def test_sqlite_effects_and_indexes_are_independently_reconciled(self):
        mutations={
            'unknown-index':"DELETE FROM idem_index WHERE key LIKE '%-1'",
            'extra-index':"INSERT INTO idem_index VALUES('attacker',1,1793640600000)",
            'wrong-index-generation':"UPDATE idem_index SET generation=2 WHERE key LIKE '%-0'",
            'expired-before-proof':f'UPDATE idem_index SET expires_ms={STAMP-1} WHERE key LIKE \'%-0\'',
            'effect-after-signed':f'UPDATE effect SET created_ms={STAMP+1} WHERE key LIKE \'%-0\'',
            'wrong-outcome':"UPDATE effect SET outcome='REJECTED' WHERE key LIKE '%-0'",
            'duplicate-effect':f"INSERT INTO effect VALUES('{RUN}-0',2,'ACTIVATED',{STAMP-1000})",
            'unknown-effect':f"INSERT INTO effect VALUES('{RUN}-9',1,'ACTIVATED',{STAMP-1000})",
        }
        for label,statement in mutations.items():
            with self.subTest(label=label):
                p=self._database(lambda db, s=statement: db.execute(s))
                with self.assertRaises(ValueError):
                    audit_sqlite_index_and_proof_times(p,self._signed(),RUN,2)

    def test_proof_time_missing_duplicate_or_invalid(self):
        p=self._database()
        evidence=self._signed()
        for data in ('',evidence.splitlines()[0],
                     evidence.replace(f'{RUN}-1',f'{RUN}-0'),
                     evidence.replace(str(STAMP),'0',1),
                     evidence.replace(str(STAMP),'banana',1),
                     evidence.replace(f'{RUN}-1',f'{RUN}-9')):
            with self.subTest(data=data[:45]),self.assertRaises(ValueError):
                audit_sqlite_index_and_proof_times(p,data,RUN,2)

    def test_probe_is_read_only(self):
        p=self._database();audit_sqlite_index_and_proof_times(p,self._signed(),RUN,2)
        with closing(sqlite3.connect(p)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM effect').fetchone()[0],2)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM idem_index').fetchone()[0],2)

if __name__=='__main__':unittest.main()

class PublicAdmissionPayloadTests(unittest.TestCase):
    @staticmethod
    def _rows():
        from hashlib import sha256
        return '\n'.join(f'{RUN}-{i}|c-{i}|standard|public:'+
                         sha256(f'c-{i}\0standard'.encode()).hexdigest() for i in range(2))

    def test_baseline_payload_and_fingerprints(self):
        from gateway_lineage_audit import audit_payload_rows,postgres_payload_query
        self.assertEqual(audit_payload_rows(self._rows(),RUN,2,{'0':0,'1':0})['durable_payload_fingerprints_verified'],2)
        q=postgres_payload_query('gw_proof',RUN)
        self.assertIn('r.fingerprint',q)
        self.assertIn('r.customer_id',q)
        self.assertNotIn('UPDATE ',q)

    def test_conflicting_payload_and_digest_mutations_fail(self):
        from gateway_lineage_audit import audit_payload_rows
        good=self._rows()
        for label,altered in {
            'wrong-product':good.replace('standard','different',1),
            'wrong-customer':good.replace('c-0','c-0-conflict',1),
            'wrong-fingerprint':good.replace('public:','public:bad',1),
            'duplicate':good.replace(f'{RUN}-1',f'{RUN}-0'),
            'missing':good.splitlines()[0],
            'nondecimal-slot':good.replace(f'{RUN}-1',f'{RUN}-01'),
            'unexpected-extra':good+'\nother|other|other|other'
        }.items():
            with self.subTest(label=label),self.assertRaises(ValueError):
                audit_payload_rows(altered,RUN,2,{'0':0,'1':0})

class ArrivalOrderPayloadTests(unittest.TestCase):
    def test_variant_one_can_win_if_it_arrives_first(self):
        from gateway_lineage_audit import audit_payload_rows
        from hashlib import sha256
        customer='c-0-conflict';product='different'
        fingerprint='public:'+sha256((customer+'\0'+product).encode()).hexdigest()
        row=f'{RUN}-0|{customer}|{product}|{fingerprint}'
        self.assertEqual(audit_payload_rows(row,RUN,1,{'0':1})['durable_payload_fingerprints_verified'],1)
        with self.assertRaises(ValueError):audit_payload_rows(row,RUN,1,{'0':0})

    def test_winner_vector_is_complete_and_literal(self):
        from gateway_lineage_audit import audit_payload_rows
        rows=PublicAdmissionPayloadTests._rows()
        for invalid in ({'0':0}, {'0':0,'1':True}, {'0':0,'1':2}, {'0':0,'1':0,'2':0}):
            with self.subTest(invalid=invalid),self.assertRaises(ValueError):
                audit_payload_rows(rows,RUN,2,invalid)
