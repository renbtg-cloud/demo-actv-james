#!/usr/bin/env python3
"""Independently verifiable Ed25519 receipt for a collector-approved result.

A receipt authenticates an explicitly pinned collector's claim about the exact
bytes of its audited output. It does not prove uncompromised processes, host
isolation, honest remote actors, or universally exactly-once effects.
"""
import argparse
import base64
import binascii
import hashlib
import json
import pathlib
import stat
import sys

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey,Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key,load_pem_public_key
from host_session import canonical,digest,read_json
from host_signed_evidence import _read_bounded

_NAMESPACE=b'deroyque-collector-receipt-v1\x00'
_NOTE='PINNED_COLLECTOR_SIGNED_EXACT_BYTES_NOT_HARDWARE_ATTESTATION'


def _candidate(result,session):
    if (result.get('outcome')!='PASS' or result.get('run_id')!=session.get('run_id') or
        result.get('session_sha256')!=digest(session) or
        result.get('harness_errors') != [] or
        result.get('global_invariant_violations') != [] or
        result.get('state_verification_errors') != [] or
        result.get('unexpected_http_or_transport')!=0 or
        not isinstance(result.get('signed_host_evidence'),dict) or
        result['signed_host_evidence'].get('status')!='ALL_PINNED_SIGNING_KEYS_VERIFIED_NOT_HOST_ATTESTATION'):
        raise ValueError('Collector receipt requires a complete successful signed verdict')


def create_receipt(result_path,session_path,private_key_path):
    raw=_read_bounded(result_path,2*1024*1024)
    result=json.loads(raw)
    session=read_json(session_path)
    if not isinstance(result,dict):raise ValueError('Invalid verdict document')
    _candidate(result,session)
    keypath=pathlib.Path(private_key_path)
    if keypath.is_symlink() or not keypath.is_file() or keypath.stat().st_mode & (stat.S_IRWXG|stat.S_IRWXO):
        raise ValueError('Collector private key must be owner-only')
    key=load_pem_private_key(_read_bounded(keypath,128*1024),password=None)
    if not isinstance(key,Ed25519PrivateKey):raise ValueError('Require Ed25519 private key')
    payload={'schema_version':1,'run_id':session['run_id'],
             'session_sha256':digest(session),'result_sha256':hashlib.sha256(raw).hexdigest(),
             'claim':_NOTE}
    return {'payload':payload,
            'signature':base64.b64encode(key.sign(_NAMESPACE+canonical(payload))).decode('ascii')}


def verify_receipt(result_path,session_path,receipt_path,public_key_path):
    session=read_json(session_path)
    raw=_read_bounded(result_path,2*1024*1024)
    result=json.loads(raw)
    if not isinstance(result,dict):raise ValueError('Invalid verdict')
    _candidate(result,session)
    report=read_json(receipt_path,128*1024)
    if set(report)!={'payload','signature'} or not isinstance(report['payload'],dict):
        raise ValueError('Invalid collector receipt envelope')
    expected={'schema_version':1,'run_id':session['run_id'],
              'session_sha256':digest(session),'result_sha256':hashlib.sha256(raw).hexdigest(),
              'claim':_NOTE}
    if type(report['payload'].get('schema_version')) is not int or report['payload']!=expected:
        raise ValueError('Collector receipt contradicts exact result/session bytes')
    sig=report['signature']
    if not isinstance(sig,str) or len(sig)>128:raise ValueError('Invalid collector signature')
    try:signature=base64.b64decode(sig,validate=True)
    except binascii.Error as exc:raise ValueError('Invalid signature encoding') from exc
    if len(signature)!=64:raise ValueError('Wrong collector signature length')
    key=load_pem_public_key(_read_bounded(public_key_path,128*1024))
    if not isinstance(key,Ed25519PublicKey):raise ValueError('Expected Ed25519 collector public key')
    try:key.verify(signature,_NAMESPACE+canonical(expected))
    except InvalidSignature as exc:raise ValueError('Collector signature mismatch') from exc
    return {'status':'COLLECTOR_RECEIPT_VERIFIED_NOT_HARDWARE_ATTESTATION',
            'run_id':session['run_id'],'session_sha256':digest(session),
            'result_sha256':expected['result_sha256']}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='command',required=True)
    for mode in ('sign','verify'):
        s=sub.add_parser(mode)
        s.add_argument('--result',required=True,type=pathlib.Path)
        s.add_argument('--session',required=True,type=pathlib.Path)
        s.add_argument('--receipt',required=True,type=pathlib.Path)
        s.add_argument('--collector-key',required=True,type=pathlib.Path)
    a=p.parse_args(argv)
    try:
        if a.command=='sign':
            receipt=create_receipt(a.result,a.session,a.collector_key)
            with a.receipt.open('x',encoding='ascii') as f:
                f.write(json.dumps(receipt,sort_keys=True,indent=2)+'\n')
            print('COLLECTOR_RECEIPT_SIGNED',receipt['payload']['run_id'])
        else:
            print(json.dumps(verify_receipt(a.result,a.session,a.receipt,a.collector_key),sort_keys=True))
        return 0
    except (OSError,ValueError,TypeError,KeyError,json.JSONDecodeError,UnicodeError) as exc:
        print('COLLECTOR_RECEIPT_REJECTED',exc,file=sys.stderr)
        return 2

if __name__=='__main__':sys.exit(main())
