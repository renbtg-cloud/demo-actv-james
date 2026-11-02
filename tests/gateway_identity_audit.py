#!/usr/bin/env python3
"""Independent per-identity check for the real gateway's PostgreSQL/SQLite lab.

An equal *total* across ledgers is not a bijection: this module checks individual
keys, one-to-one reference links and each provider effect's actual generation.
The SQL is a read-only query for an ephemeral isolated test schema only.
"""
import re
import sqlite3
from pathlib import Path

_IDENTIFIER = re.compile(r'[a-z][a-z0-9_]{0,62}\Z')
_RUN = re.compile(r'[A-Za-z0-9_-]{1,48}\Z')


def expected_keys(run, count):
    if not isinstance(run, str) or not _RUN.fullmatch(run):
        raise ValueError('invalid trusted workload run ID')
    if type(count) is not int or not 1 <= count <= 1_000_000:
        raise ValueError('invalid key count')
    return {f'{run}-{slot}' for slot in range(count)}


def postgres_identity_query(schema, run):
    """Return one row per request plus correlated per-identity cardinalities.

    Correlated subqueries prevent join multiplication from concealing duplicate
    dispatches, settlements or attestations. Also inspect cross-rid references.
    """
    if not isinstance(schema, str) or not _IDENTIFIER.fullmatch(schema):
        raise ValueError('invalid schema identifier')
    expected_keys(run, 1)  # run ID contains no SQL LIKE or string escapes
    tab = lambda name: f'{schema}.{name}'
    return f"""SELECT r.idem_key, r.rid,
        (SELECT count(*) FROM {tab('p9_dispatch')} d WHERE d.rid=r.rid),
        (SELECT count(*) FROM {tab('p9_settlement')} s WHERE s.rid=r.rid),
        (SELECT count(*) FROM {tab('p20_signed_attestation')} a
           JOIN {tab('p9_dispatch')} d ON a.did=d.did WHERE d.rid=r.rid),
        (SELECT count(*) FROM {tab('p9_settlement')} s
           JOIN {tab('p9_dispatch')} d ON s.did=d.did
           WHERE s.rid=r.rid AND d.rid<>r.rid),
        (SELECT count(*) FROM {tab('p20_signed_attestation')} a
           JOIN {tab('p9_dispatch')} d ON a.did=d.did
           WHERE d.rid=r.rid AND a.issuer='deroyque-lab-provider'
             AND a.kid='lab-key-01' AND a.reported_outcome='ACTIVATED'
             AND a.effect_count=1 AND a.generation=1)
        FROM {tab('p9_requests')} r WHERE left(r.idem_key,length('{run}-'))='{run}-'
        ORDER BY r.idem_key"""


def audit_postgres_rows(rows, run, count):
    """Validate PostgreSQL psql -A output; no secrets, read-only, fail closed."""
    expected = expected_keys(run, count)
    if not isinstance(rows, str):
        raise ValueError('invalid PostgreSQL result type')
    observed = set()
    identifiers = set()
    for line in rows.splitlines():
        parts = line.split('|')
        if len(parts) != 7:
            raise ValueError('corrupt PostgreSQL identity record')
        key, rid = parts[:2]
        if key not in expected or key in observed or not re.fullmatch(r'[0-9a-fA-F-]{36}', rid):
            raise ValueError('unexpected, duplicate or invalid PostgreSQL request identity')
        if rid in identifiers:
            raise ValueError('two identities refer to same PostgreSQL request')
        try:
            values = tuple(int(x) for x in parts[2:])
        except ValueError as exc:
            raise ValueError('nonnumeric PostgreSQL relationship cardinality') from exc
        if values != (1, 1, 1, 0, 1):
            raise ValueError('non-bijective PostgreSQL dispatch/settlement/signed-proof graph')
        observed.add(key)
        identifiers.add(rid)
    if observed != expected:
        raise ValueError('missing PostgreSQL identities')
    return {'keys': len(observed), 'dispatches': len(observed),
            'settlements': len(observed), 'verified_signed_attestations': len(observed)}


def audit_sqlite_effects(path, run, count):
    """Reject even an offsetting extra/missing key; expect generation-one activation."""
    wanted = expected_keys(run, count)
    if not isinstance(path, (str, Path)) or not Path(path).is_file():
        raise ValueError('provider database absent')
    # Open read-only: audit must never initialize or alter the provider store.
    uri = Path(path).resolve().as_uri() + '?mode=ro'
    observed = set()
    try:
        with sqlite3.connect(uri, uri=True, timeout=5) as connection:
            for key, generation, outcome in connection.execute(
                    'SELECT key,generation,outcome FROM effect'):
                if (key not in wanted or key in observed or
                        type(generation) is not int or generation != 1 or outcome != 'ACTIVATED'):
                    raise ValueError('provider effects do not match one-per-key contract')
                observed.add(key)
    except sqlite3.Error as exc:
        raise ValueError('invalid provider SQLite evidence') from exc
    if observed != wanted:
        raise ValueError('missing provider effects for expected identities')
    return {'keys': len(observed), 'generation': 1, 'outcome': 'ACTIVATED'}
