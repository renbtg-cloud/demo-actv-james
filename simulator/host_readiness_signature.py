#!/usr/bin/env python3
"""Session-bound Ed25519 signatures over exact preflight bytes.

Host signatures authenticate the enrolled *key*, never its physical machine,
UTC clock, operating system, or the truth of a self-reported observation.
This namespace is intentionally separate from post-run host shard signatures.
"""
import argparse
import base64
import binascii
import hashlib
import json
import os
import pathlib
import stat
import sys

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from host_session import canonical, digest, read_json, validate
from host_signed_evidence import _pinned_keys

_DOMAIN=b'deroyque-readiness-proof-v1\x00'
_FIELDS={'schema_version','run_id','host_id','shard_index','readiness_sha256','session_sha256'}


def _strict_pairs(pairs):
    obj={}
    for k,v in pairs:
        if k in obj:raise ValueError('Duplicate JSON object key in signed readiness')
        obj[k]=v
    return obj


def _strict_document(raw):
    return json.loads(raw,object_pairs_hook=_strict_pairs,
                      parse_constant=lambda x: (_ for _ in ()).throw(ValueError('Nonfinite JSON constant')))


def _read_regular(path,max_bytes):
    # Nonblocking, no final symlink, no pipes/devices, finite size and bound to
    # one open descriptor. Parent directory ownership remains operator-managed.
    flags=os.O_RDONLY|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_NONBLOCK',0)|getattr(os,'O_CLOEXEC',0)
    fd=os.open(path,flags)
    try:
        before=os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0<before.st_size<=max_bytes:
            raise ValueError('Unsafe or oversized readiness material')
        with os.fdopen(os.dup(fd),'rb') as stream:
            raw=stream.read(max_bytes+1)
        after=os.fstat(fd)
        if (len(raw)!=before.st_size or len(raw)>max_bytes or
            (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns)!=
            (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns)):
            raise ValueError('Readiness file modified during bounded read')
        return raw,before
    finally:os.close(fd)


def _read_private_key(path):
    raw,metadata=_read_regular(path,128*1024)
    if metadata.st_mode & (stat.S_IRWXG|stat.S_IRWXO):
        raise ValueError('Signing key must be owner-only')
    key=load_pem_private_key(raw,password=None)
    if not isinstance(key,Ed25519PrivateKey):
        raise ValueError('Signing key must be Ed25519')
    return key


def make_proof(report_path, private_key_path, host_id, session, manifest, pins):
    session_hash=validate(session,manifest,pins)
    data,_=_read_regular(report_path,131072)
    report=_strict_document(data)
    shard=report.get('shard_index') if isinstance(report,dict) else None
    if (type(shard) is not int or not 0<=shard<len(session['host_assignments']) or
        session['host_assignments'][shard]!=host_id or
        report.get('run_id')!=session['run_id'] or
        report.get('manifest_sha256')!=digest(manifest)):
        raise ValueError('Readiness identity/manifest differs from collector assignment')
    key=_read_private_key(private_key_path)
    # Refuse accidentally signing with a key other than the enrolled host key.
    from cryptography.hazmat.primitives import serialization
    enrolled=_pinned_keys(pins)[host_id]
    raw_key=key.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)
    pinned_key=enrolled.public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)
    if raw_key!=pinned_key:raise ValueError('Private key differs from collector-enrolled host key')
    payload={'schema_version':1,'run_id':session['run_id'],'host_id':host_id,
             'shard_index':shard,'readiness_sha256':hashlib.sha256(data).hexdigest(),
             'session_sha256':session_hash}
    signature=key.sign(_DOMAIN+canonical(payload))
    return {'payload':payload,'signature':base64.b64encode(signature).decode('ascii')}


def verify_proofs(report_bytes_by_shard,proof_paths,session,manifest,pins):
    """Refuse incomplete sets, substitutions, and report/signature mix-and-match."""
    session_hash=validate(session,manifest,pins)
    keys=_pinned_keys(pins)
    n=len(session['host_assignments'])
    if (not isinstance(report_bytes_by_shard,dict) or set(report_bytes_by_shard)!=set(range(n)) or
        len(proof_paths)!=n):
        raise ValueError('Complete signed readiness and matching reports required')
    verified={}
    for path in proof_paths:
        proof=_strict_document(_read_regular(path,16384)[0]) if isinstance(path,(str,pathlib.Path)) else path
        if not isinstance(proof,dict) or set(proof)!={'payload','signature'}:
            raise ValueError('Malformed signed readiness envelope')
        payload=proof['payload']
        if not isinstance(payload,dict) or set(payload)!=_FIELDS:
            raise ValueError('Malformed signed readiness payload')
        shard=payload['shard_index']
        if type(shard) is not int or not 0<=shard<n or shard in verified:
            raise ValueError('Duplicate or invalid signed readiness shard')
        if (type(payload['schema_version']) is not int or payload['schema_version']!=1 or
            payload['run_id']!=session['run_id'] or
            payload['host_id']!=session['host_assignments'][shard] or
            payload['session_sha256']!=session_hash or
            payload['readiness_sha256']!=hashlib.sha256(report_bytes_by_shard[shard]).hexdigest()):
            raise ValueError('Signed readiness content/session/host mismatch')
        signature=proof['signature']
        if not isinstance(signature,str) or len(signature)!=88:
            raise ValueError('Malformed Ed25519 readiness signature')
        try:
            raw=base64.b64decode(signature,validate=True)
        except (binascii.Error,ValueError) as exc:
            raise ValueError('Malformed base64 readiness signature') from exc
        if len(raw)!=64 or base64.b64encode(raw).decode('ascii')!=signature:
            raise ValueError('Noncanonical readiness signature')
        try:
            keys[payload['host_id']].verify(raw,_DOMAIN+canonical(payload))
        except InvalidSignature as exc:
            raise ValueError('Invalid pinned-host readiness signature') from exc
        verified[shard]={'shard_index':shard,'host_id':payload['host_id'],
                          'readiness_sha256':payload['readiness_sha256'],
                          'signed_payload_sha256':digest(payload)}
    if set(verified)!=set(range(n)):
        raise ValueError('Incomplete signed readiness host coverage')
    return {'status':'ALL_PINNED_KEYS_VERIFIED_NOT_HOST_ATTESTATION',
            'session_sha256':session_hash,
            'verified_shards':[verified[i] for i in range(n)],
            'note':'Signatures bind exact report bytes and collector-enrolled keys, not physical hosts or truthful self-reports'}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--report',type=pathlib.Path,required=True)
    p.add_argument('--private-key',type=pathlib.Path,required=True)
    p.add_argument('--host-id',required=True)
    p.add_argument('--session',type=pathlib.Path,required=True)
    p.add_argument('--manifest',type=pathlib.Path,required=True)
    p.add_argument('--pins',type=pathlib.Path,required=True)
    p.add_argument('--output',type=pathlib.Path,required=True)
    a=p.parse_args(argv)
    try:
        proof=make_proof(a.report,a.private_key,a.host_id,
                         read_json(a.session),read_json(a.manifest,2*1024*1024),read_json(a.pins))
        # Same no-replace, fsynced owner-only writer as the collector verdict.
        from host_matrix import _publish_verdict
        _publish_verdict(a.output,proof)
        print('READINESS_PINNED_KEY_PROOF_CREATED',proof['payload']['host_id'],proof['payload']['shard_index'])
        return 0
    except (OSError,ValueError,TypeError,KeyError,UnicodeError,json.JSONDecodeError,LookupError) as exc:
        print('READINESS_SIGNING_REJECTED',exc,file=sys.stderr)
        return 2

if __name__=='__main__':sys.exit(main())
