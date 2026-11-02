#!/usr/bin/env python3
"""Read-only, per-run provenance and cross-store checks for the disposable gateway lab.

Each query is deliberately limited to the freshly created PostgreSQL schema.
The verifier has no write path. These checks do not imply a global transaction
across PostgreSQL and the separately persisted SQLite provider.
"""
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from gateway_identity_audit import expected_keys, postgres_identity_query


def _safe(schema, run):
    # Reuse the approved strict identifiers instead of duplicating SQL escaping.
    postgres_identity_query(schema, run)


def postgres_lineage_query(schema, run):
    """Read one row per key with terminal and signed-observation link counts."""
    _safe(schema, run)
    return f"""SELECT r.idem_key, r.rid, r.stage,
        (SELECT count(*) FROM {schema}.p9_settlement s
         JOIN {schema}.p9_dispatch d ON s.did=d.did
         WHERE s.rid=r.rid AND d.rid=r.rid AND s.target='DONE'),
        (SELECT count(*) FROM {schema}.p20_signed_attestation a
         JOIN {schema}.p9_dispatch d ON a.did=d.did
         JOIN {schema}.p9_observation o ON a.oid=o.oid
         WHERE d.rid=r.rid AND o.did=d.did
           AND o.source='SIGNED_PROVIDER_LOOKUP' AND o.http_status=200
           AND o.action IN ('ADOPT_ACTIVATED','CONTRACT_SUCCESS')
           AND o.evidence_level IN ('SIGNED_LEDGER_CLAIM','STATUS_CONTRACT')),
        (SELECT count(*) FROM {schema}.p20_signed_attestation a
         JOIN {schema}.p9_dispatch d ON a.did=d.did
         JOIN {schema}.p9_observation o ON a.oid=o.oid
         WHERE d.rid=r.rid AND (o.did<>a.did OR o.source<>'SIGNED_PROVIDER_LOOKUP'))
        FROM {schema}.p9_requests r
        WHERE left(r.idem_key,length('{run}-'))='{run}-'
        ORDER BY r.idem_key"""


def audit_lineage_rows(rows, run, count):
    expected = expected_keys(run, count)
    if not isinstance(rows, str):
        raise ValueError('lineage result must be text')
    observed = set()
    for line in rows.splitlines():
        cols = line.split('|')
        if len(cols) != 6:
            raise ValueError('malformed lineage row')
        key, rid, stage, settlement, signed, bad = cols
        if (key not in expected or key in observed or
                re.fullmatch(r'[0-9a-fA-F-]{36}',rid) is None or stage!='DONE'):
            raise ValueError('missing, duplicate or nonterminal lineage')
        if (settlement,signed,bad) != ('1','1','0'):
            raise ValueError('incorrect terminal or signed-observation lineage')
        observed.add(key)
    if observed != expected:
        raise ValueError('missing lineage keys')
    return {'verified_terminal_lineages':len(observed)}


def schema_cardinality_query(schema, run):
    _safe(schema, run)
    return f"""SELECT
      (SELECT count(*) FROM {schema}.p9_requests),
      (SELECT count(*) FROM {schema}.p9_dispatch),
      (SELECT count(*) FROM {schema}.p9_settlement),
      (SELECT count(*) FROM {schema}.p20_signed_attestation),
      (SELECT count(*) FROM {schema}.p14_evidence_incident),
      (SELECT count(*) FROM {schema}.p20_rejected_attestation)"""


def audit_schema_cardinality(rows, count):
    if type(count) is not int or count < 1 or not isinstance(rows,str):
        raise ValueError('invalid schema cardinality arguments')
    cols=rows.strip().split('|')
    if len(cols)!=6 or any(not re.fullmatch(r'0|[1-9][0-9]*',x) for x in cols):
        raise ValueError('invalid PostgreSQL cardinality snapshot')
    values=tuple(map(int,cols))
    if values!=(count,count,count,count,0,0):
        raise ValueError('extra/missing schema-wide rows or rejected attestation')
    return {'clean_schema_keys':count,'unexpected_incidents':0,'unexpected_rejections':0}


def audit_sqlite_index_and_proof_times(database, signed_rows, run, count):
    """Cross-check independent provider index, effects, and signed claims.

    The checked timestamps are from *the same test host*: an intentionally
    narrower assertion than a claim about clocks on separate physical machines.
    """
    expected=expected_keys(run,count)
    if not isinstance(signed_rows,str) or not isinstance(database,(str,Path)) or not Path(database).is_file():
        raise ValueError('missing provider or signed proof evidence')
    stamps={}
    for line in signed_rows.splitlines():
        cols=line.split('|')
        if len(cols)!=4 or cols[0] not in expected or cols[0] in stamps:
            raise ValueError('untrusted signed record identity')
        try:stamp=int(cols[3])
        except ValueError as exc:raise ValueError('malformed signed timestamp') from exc
        if stamp<=0 or str(stamp)!=cols[3]:raise ValueError('invalid signed timestamp')
        stamps[cols[0]]=stamp
    if stamps.keys()!=expected:raise ValueError('incomplete signed times')
    uri=Path(database).resolve().as_uri()+'?mode=ro'
    seen=set()
    try:
        with closing(sqlite3.connect(uri,uri=True,timeout=5)) as conn:
            query="""SELECT e.key,e.generation,e.outcome,e.created_ms,
                     i.generation,i.expires_ms
                     FROM effect e LEFT JOIN idem_index i ON i.key=e.key"""
            for key,gen,outcome,created,idx_gen,expires in conn.execute(query):
                if (key not in expected or key in seen or
                        type(gen) is not int or gen!=1 or outcome!='ACTIVATED' or
                        type(idx_gen) is not int or idx_gen!=1 or
                        type(created) is not int or type(expires) is not int or
                        not 0<created<=stamps[key]<expires):
                    raise ValueError('provider index/effect/signed time contradiction')
                seen.add(key)
            index_rows=conn.execute('SELECT count(*) FROM idem_index').fetchone()[0]
    except sqlite3.Error as exc:
        raise ValueError('invalid SQLite index/effect evidence') from exc
    if seen!=expected or index_rows!=count:
        raise ValueError('provider index/effect identity mismatch')
    return {'indexed_effect_proofs':count,'time_scope':'single_host_provider_clock'}


def postgres_payload_query(schema, run):
    """Read exact stored public admission data for the deterministic lab inputs."""
    _safe(schema,run)
    return f"""SELECT r.idem_key,r.customer_id,r.product_code,r.fingerprint
        FROM {schema}.p9_requests r
        WHERE left(r.idem_key,length('{run}-'))='{run}-'
        ORDER BY r.idem_key"""


def audit_payload_rows(rows, run, count, winners):
    """Independently recompute PublicRequests SHA256 customer-NUL-product.

    The verified producer responses determine which variant won each key.
    Schedule order alone cannot prove arrival order across concurrent JVMs.
    Any later conflicting payload must not replace the observed winner.
    This test is not a generic production business-payload validator.
    """
    import hashlib
    expected=expected_keys(run,count)
    if (not isinstance(winners,dict) or len(winners)!=count or
            any(str(i) not in winners or type(winners[str(i)]) is not int or
                winners[str(i)] not in (0,1) for i in range(count))):
        raise ValueError('missing independently reconciled winner variants')
    if not isinstance(rows,str):raise ValueError('invalid public payload rows')
    seen=set()
    for line in rows.splitlines():
        cols=line.split('|')
        if len(cols)!=4:raise ValueError('malformed public payload row')
        key,customer,product,fingerprint=cols
        if key not in expected or key in seen:
            raise ValueError('unknown or duplicated payload identity')
        # Strip only the exact trusted prefix; key-space is based on slot numbers.
        slot=int(key[len(run)+1:]) if key[len(run)+1:].isdecimal() else -1
        if slot<0 or slot>=count or key!=f'{run}-{slot}':
            raise ValueError('invalid workload slot')
        variant=winners[str(slot)]
        if (customer!=f'c-{slot}'+('-conflict' if variant else '') or
                product!=('different' if variant else 'standard')):
            raise ValueError('accepted conflicting payload or wrong materialized data')
        digest=hashlib.sha256((customer+'\0'+product).encode('utf-8')).hexdigest()
        if fingerprint!='public:'+digest:
            raise ValueError('wrong durable idempotency fingerprint')
        seen.add(key)
    if seen!=expected:raise ValueError('missing public admission payload')
    return {'durable_payload_fingerprints_verified':count}
