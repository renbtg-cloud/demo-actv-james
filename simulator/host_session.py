#!/usr/bin/env python3
"""Collector-generated, one-run signing session: manifest/host/key pins bound.

Trust is anchored in the *collector's offline custody* of session, manifest and
public-key inventory. This is NOT physical host attestation or a trusted clock.
"""
import argparse
import hashlib
import json
import pathlib
import re
import secrets
import sys

_ID = re.compile(r'[A-Za-z0-9_-]{1,48}\Z')
_HEX = re.compile(r'[0-9a-f]{64}\Z')


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                      allow_nan=False).encode('ascii')


def digest(obj):
    return hashlib.sha256(canonical(obj)).hexdigest()


def read_json(path, limit=131072):
    path = pathlib.Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise ValueError('Unsafe or oversized collector-held JSON')
    obj = json.loads(path.read_bytes())
    if not isinstance(obj, dict):
        raise ValueError('Collector document must be a JSON object')
    return obj


def validate(session, manifest, pins):
    from host_signed_evidence import _pinned_keys
    keys = _pinned_keys(pins)
    if not isinstance(session, dict) or set(session) != {
        'schema_version', 'run_id', 'nonce', 'manifest_sha256',
        'pins_sha256', 'host_assignments'}:
        raise ValueError('Invalid signing-session schema')
    if type(session['schema_version']) is not int or session['schema_version'] != 1:
        raise ValueError('Unknown signing-session version')
    run_id = session['run_id']
    if not isinstance(run_id,str) or not _ID.fullmatch(run_id):
        raise ValueError('Invalid signing-session run ID')
    if not isinstance(session['nonce'],str) or not _HEX.fullmatch(session['nonce']):
        raise ValueError('Session challenge must be 256-bit lowercase hex')
    assignments = session['host_assignments']
    if (not isinstance(assignments,list) or not 2<=len(assignments)<=256 or
        any(not isinstance(h,str) or h not in keys for h in assignments) or
        len(set(assignments))!=len(assignments) or set(assignments)!=set(keys)):
        raise ValueError('Host assignments must be a bijection to pinned keys')
    if (not isinstance(manifest,dict) or manifest.get('schema_version')!=2 or
        manifest.get('run_id')!=run_id or type(manifest.get('shards')) is not int or
        manifest['shards'] != len(assignments)):
        raise ValueError('Session contradicts launch manifest')
    if (session['manifest_sha256'] != digest(manifest) or
        session['pins_sha256'] != digest(pins)):
        raise ValueError('Session differs from trusted launch manifest or pinned keys')
    return digest(session)


def create_session(manifest,pins,assignments):
    if not isinstance(assignments,list) or not isinstance(manifest,dict):
        raise ValueError('Invalid collector inputs')
    result={'schema_version':1,'run_id':manifest.get('run_id'),
            'nonce':secrets.token_hex(32),'manifest_sha256':digest(manifest),
            'pins_sha256':digest(pins),'host_assignments':assignments}
    validate(result,manifest,pins)
    return result


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',required=True,type=pathlib.Path)
    p.add_argument('--pins',required=True,type=pathlib.Path)
    p.add_argument('--hosts',required=True,help='Host IDs in shard-index order, comma-separated')
    p.add_argument('--output',required=True,type=pathlib.Path)
    a=p.parse_args(argv)
    try:
        if a.output.exists() or a.output.is_symlink():
            raise ValueError('Never overwrite a prior collector session')
        manifest=read_json(a.manifest)
        pins=read_json(a.pins)
        session=create_session(manifest,pins,a.hosts.split(','))
        # Exclusive creation prevents accidental challenge overwrite.
        with a.output.open('x',encoding='ascii') as f:
            f.write(json.dumps(session,sort_keys=True,indent=2)+'\n')
        print('SIGNING_SESSION_CREATED',session['run_id'],digest(session))
        return 0
    except (ValueError,OSError,TypeError,KeyError,UnicodeError,json.JSONDecodeError) as exc:
        print('SIGNING_SESSION_REJECTED',exc,file=sys.stderr)
        return 2

if __name__=='__main__':sys.exit(main())
