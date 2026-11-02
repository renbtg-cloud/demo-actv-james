#!/usr/bin/env python3
"""Optional one-time collector acceptance journal for verified signing sessions.

It prevents accidental or malicious *republication* of the same session through
this collector database. It does not prove first execution, remote host identity,
or global uniqueness across collectors that do not share this database.
"""
import argparse
import datetime
import json
import os
import pathlib
import sqlite3
import sys

from host_session import read_json, digest


def record_once(db_path, session, verified_merge):
    if (not isinstance(verified_merge,dict) or verified_merge.get('outcome')!='PASS' or
        verified_merge.get('run_id')!=session.get('run_id') or
        not isinstance(verified_merge.get('signed_host_evidence'),dict) or
        verified_merge['signed_host_evidence'].get('status')!='ALL_PINNED_SIGNING_KEYS_VERIFIED_NOT_HOST_ATTESTATION' or
        not isinstance(verified_merge.get('session_sha256'),str) or
        verified_merge['session_sha256']!=digest(session)):
        raise ValueError('Refusing replay-ledger entry without matching fully verified signed session')
    path=pathlib.Path(db_path)
    if path.is_symlink() or path.is_dir():
        raise ValueError('Unsafe collector replay-ledger path')
    if not path.parent.exists() or not path.parent.is_dir() or path.parent.is_symlink():
        raise ValueError('Missing/unsafe ledger directory')
    # The collector must securely own this filesystem and DB. SQLite serializes
    # independent processes with BEGIN IMMEDIATE + a UNIQUE constraint.
    conn=sqlite3.connect(str(path), timeout=15, isolation_level=None)
    try:
        conn.execute('PRAGMA busy_timeout=15000')
        conn.execute('CREATE TABLE IF NOT EXISTS used_session ('
                     'session_sha256 TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE, '
                     'accepted_utc TEXT NOT NULL)')
        conn.execute('BEGIN IMMEDIATE')
        conn.execute('INSERT INTO used_session VALUES (?,?,?)',(
            digest(session),session['run_id'],datetime.datetime.now(datetime.timezone.utc).isoformat()))
        conn.execute('COMMIT')
    except sqlite3.IntegrityError as exc:
        if conn.in_transaction:conn.execute('ROLLBACK')
        raise ValueError('Collector session or run ID already accepted (replay)') from exc
    except Exception:
        if conn.in_transaction:conn.execute('ROLLBACK')
        raise
    finally:
        conn.close()
    return {'status':'COLLECTOR_SESSION_CONSUMED_ONCE','run_id':session['run_id'],
            'session_sha256':digest(session)}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--ledger',type=pathlib.Path,required=True)
    p.add_argument('--session',type=pathlib.Path,required=True)
    p.add_argument('--verified-merge',type=pathlib.Path,required=True)
    a=p.parse_args(argv)
    try:
        session=read_json(a.session)
        result=read_json(a.verified_merge,2*1024*1024)
        print(json.dumps(record_once(a.ledger,session,result),sort_keys=True))
        return 0
    except (ValueError,OSError,sqlite3.Error,TypeError,KeyError,json.JSONDecodeError) as exc:
        print('COLLECTOR_REPLAY_REJECTED',exc,file=sys.stderr)
        return 2

if __name__=='__main__':sys.exit(main())
