#!/usr/bin/env python3
"""Collector Ed25519 checkpoint of an independently reverified signed preflight.

A pinned collector signature authenticates *that collector's recorded bytes*,
not their true creation time, truthful self-reported host metadata, physical
host isolation, or successful later provider side effects. Key custody and
independent enrollment remain essential operator responsibilities.
"""
import argparse
import base64
import binascii
import hashlib
import json
import pathlib
import sys

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key

from host_session import canonical,digest,read_json,validate
from host_readiness_signature import _read_regular,_read_private_key,_strict_document
from readiness_handoff import reproduce_preflight

_DOMAIN=b'deroyque-readiness-checkpoint-v1\x00'
_CLAIM='PINNED_COLLECTOR_ATTESTS_STORED_PREFLIGHT_BYTES_NOT_PHYSICAL_HOSTS_OR_TRUSTED_CLOCK'


def _payload(raw,session,manifest,pins):
    return {'schema_version':1,'run_id':session['run_id'],
            'session_sha256':validate(session,manifest,pins),
            'manifest_sha256':digest(manifest),
            'matrix_sha256':hashlib.sha256(raw).hexdigest(),
            'claim':_CLAIM}


def sign_checkpoint(matrix_path, report_paths, proof_paths, session, manifest, pins, private_key_path):
    reproduced=reproduce_preflight(matrix_path,report_paths,proof_paths,manifest,pins,session)
    raw,_=_read_regular(matrix_path,2*1024*1024)
    if _strict_document(raw)!=reproduced:
        raise ValueError('Preflight matrix modified after validation')
    payload=_payload(raw,session,manifest,pins)
    key=_read_private_key(private_key_path)
    return {'payload':payload,'signature':base64.b64encode(key.sign(_DOMAIN+canonical(payload))).decode('ascii')}


def verify_checkpoint(matrix_path, receipt_path, public_key_path, report_paths,
                      proof_paths, session, manifest, pins):
    reproduced=reproduce_preflight(matrix_path,report_paths,proof_paths,manifest,pins,session)
    raw,_=_read_regular(matrix_path,2*1024*1024)
    if _strict_document(raw)!=reproduced:
        raise ValueError('Preflight matrix modified after validation')
    expected=_payload(raw,session,manifest,pins)
    envelope=_strict_document(_read_regular(receipt_path,16384)[0])
    if not isinstance(envelope,dict) or set(envelope)!={'payload','signature'} or envelope['payload']!=expected:
        raise ValueError('Collector checkpoint differs from exact preflight/session bytes')
    payload=envelope['payload']
    if not isinstance(payload,dict) or type(payload['schema_version']) is not int or payload['schema_version']!=1:
        raise ValueError('Malformed checkpoint schema')
    encoded=envelope['signature']
    if not isinstance(encoded,str) or len(encoded)!=88:
        raise ValueError('Invalid collector checkpoint signature encoding')
    try:
        signature=base64.b64decode(encoded,validate=True)
    except (binascii.Error,ValueError) as e:
        raise ValueError('Invalid collector checkpoint base64') from e
    if len(signature)!=64 or base64.b64encode(signature).decode('ascii')!=encoded:
        raise ValueError('Noncanonical collector checkpoint signature')
    public,_=_read_regular(public_key_path,128*1024)
    key=load_pem_public_key(public)
    if not isinstance(key,Ed25519PublicKey):
        raise ValueError('Collector must use Ed25519 public key')
    try:
        key.verify(signature,_DOMAIN+canonical(expected))
    except InvalidSignature as e:
        raise ValueError('Invalid independently pinned collector checkpoint signature') from e
    return {'status':'PINNED_COLLECTOR_CHECKPOINT_VERIFIED_NOT_TRUSTED_TIME_OR_HARDWARE_ATTESTATION',
            'run_id':session['run_id'],'session_sha256':expected['session_sha256'],
            'matrix_sha256':expected['matrix_sha256']}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='command',required=True)
    for action in ('sign','verify'):
        c=sub.add_parser(action)
        c.add_argument('--matrix',required=True,type=pathlib.Path)
        c.add_argument('--report',required=True,type=pathlib.Path,action='append')
        c.add_argument('--signed-readiness',required=True,type=pathlib.Path,action='append')
        c.add_argument('--session',required=True,type=pathlib.Path)
        c.add_argument('--manifest',required=True,type=pathlib.Path)
        c.add_argument('--pins',required=True,type=pathlib.Path)
        c.add_argument('--collector-key',required=True,type=pathlib.Path)
        c.add_argument('--receipt',required=True,type=pathlib.Path)
    a=p.parse_args(argv)
    try:
        session=read_json(a.session);manifest=read_json(a.manifest,2*1024*1024);pins=read_json(a.pins)
        if a.command=='sign':
            result=sign_checkpoint(a.matrix,a.report,a.signed_readiness,
                                   session,manifest,pins,a.collector_key)
            from host_matrix import _publish_verdict
            _publish_verdict(a.receipt,result)
            print('SIGNED_READINESS_CHECKPOINT_CREATED',session['run_id'])
        else:
            result=verify_checkpoint(a.matrix,a.receipt,a.collector_key,a.report,
                                     a.signed_readiness,session,manifest,pins)
            print(json.dumps(result,sort_keys=True))
        return 0
    except (ValueError,TypeError,KeyError,OSError,UnicodeError,LookupError,json.JSONDecodeError) as exc:
        print('READINESS_CHECKPOINT_REJECTED',exc,file=sys.stderr)
        return 2

if __name__=='__main__':sys.exit(main())
