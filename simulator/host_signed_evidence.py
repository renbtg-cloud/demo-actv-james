#!/usr/bin/env python3
"""Per-shard Ed25519 provenance tied to collector-pinned *public* keys.

Signing proves control of a pinned key, not independent physical machines.
Never embed private keys in the repository or a collected evidence archive.
"""
import argparse
import base64
import binascii
import hashlib
import json
import os
import pathlib
import re
import stat
import sys

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key, load_pem_public_key

_ID = re.compile(r'[A-Za-z0-9_-]{1,48}\Z')
_MEMBER = re.compile(r'agent-[0-9]{3}\.tsv\Z')
_HEX = re.compile(r'[a-f0-9]{64}\Z')
_NAMESPACE = b'deroyque-host-evidence-v1\x00'
_MAX_SUMMARY = 2*1024*1024
_MAX_EVIDENCE_FILE = 128*1024*1024
_MAX_REPORT = 64*1024


def _read_bounded(path, limit, reject_symlink=True):
    p=pathlib.Path(path)
    if (reject_symlink and p.is_symlink()) or not p.is_file() or p.stat().st_size > limit:
        raise ValueError('Missing, unsafe or oversized evidence file')
    return p.read_bytes()


def _sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _canonical(obj):
    return json.dumps(obj,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode('ascii')


def _folder_payload(folder, run_id, host_id, shard_index):
    if not _ID.fullmatch(run_id) or not _ID.fullmatch(host_id) or type(shard_index) is not int or not 0<=shard_index<256:
        raise ValueError('Invalid host/run/shard identity')
    folder=pathlib.Path(folder)
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError('Unsafe shard directory')
    raw=_read_bounded(folder/'summary.json',_MAX_SUMMARY)
    summary=json.loads(raw)
    if not isinstance(summary,dict) or not isinstance(summary.get('plan'),dict):
        raise ValueError('Invalid shard summary object/plan')
    plan=summary['plan']
    if (summary.get('run_id')!=run_id or plan.get('shard_index')!=shard_index or
        type(plan.get('shard_index')) is not int):
        raise ValueError('Shard summary conflicts with signed run identity')
    jvms=plan.get('jvms')
    if type(jvms) is not int or not 1<=jvms<=256:
        raise ValueError('Invalid JVM count')
    expected={f'agent-{i:03d}.tsv' for i in range(jvms)}
    actual={p.name for p in folder.glob('agent-*.tsv')}
    if actual!=expected:raise ValueError('Unexpected or missing agent TSV')
    members={}
    total_bytes=0
    for name in sorted(expected):
        if not _MEMBER.fullmatch(name):raise ValueError('Invalid evidence member')
        raw_agent=_read_bounded(folder/name,_MAX_EVIDENCE_FILE)
        total_bytes += len(raw_agent)
        if total_bytes > 512*1024*1024:
            raise ValueError('Total signed shard evidence exceeds 512 MiB')
        members[name]=_sha_bytes(raw_agent)
    return {'schema_version':1,'run_id':run_id,'host_id':host_id,'shard_index':shard_index,
            'summary_sha256':_sha_bytes(raw),'agent_sha256':members}


def make_envelope(folder, run_id, host_id, shard_index, private_key_path,
                  session=None, manifest=None, pins=None, clock_report=None):
    p=pathlib.Path(private_key_path)
    if p.is_symlink() or not p.is_file() or p.stat().st_mode & (stat.S_IRWXG|stat.S_IRWXO):
        raise ValueError('Private key must be a regular owner-only file')
    private=load_pem_private_key(_read_bounded(p,128*1024),password=None)
    if not isinstance(private,Ed25519PrivateKey):
        raise ValueError('Require Ed25519 private key')
    payload=_folder_payload(folder,run_id,host_id,shard_index)
    if session is not None:
        from host_session import validate
        if manifest is None or pins is None:
            raise ValueError('Session-bound signature requires launch manifest and pinned keys')
        challenge_digest=validate(session,manifest,pins)
        if payload['run_id'] != session['run_id'] or session['host_assignments'][shard_index] != host_id:
            raise ValueError('Signing identity differs from collector session')
        payload['session_sha256']=challenge_digest
    if clock_report is not None:
        if session is None:
            raise ValueError('Clock-report signature requires collector session')
        raw_clock=_read_bounded(clock_report,512*1024)
        clock=json.loads(raw_clock)
        if (not isinstance(clock,dict) or clock.get('schema_version')!=1 or
            clock.get('run_id')!=run_id or type(clock.get('shard_index')) is not int or
            clock['shard_index']!=shard_index):
            raise ValueError('Signed clock-report run/shard mismatch')
        payload['clock_report_sha256']=_sha_bytes(raw_clock)
    sig=private.sign(_NAMESPACE+_canonical(payload))
    return {'payload':payload,'signature':base64.b64encode(sig).decode('ascii')}


def _pinned_keys(pins):
    if not isinstance(pins,dict) or not 2<=len(pins)<=256:
        raise ValueError('At least two pinned host public keys required')
    keys={};fingerprints=set()
    for host_id,pem in pins.items():
        if not isinstance(host_id,str) or not _ID.fullmatch(host_id) or not isinstance(pem,str):
            raise ValueError('Invalid pin identity/material')
        key=load_pem_public_key(pem.encode('ascii'))
        if not isinstance(key,Ed25519PublicKey):raise ValueError('Expected Ed25519 public key')
        from cryptography.hazmat.primitives import serialization
        raw=key.public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)
        finger=_sha_bytes(raw)
        if finger in fingerprints:raise ValueError('One pinned signing key reused for multiple hosts')
        fingerprints.add(finger);keys[host_id]=key
    return keys


def verify_envelopes(pins,report_paths,shard_folders,expected_run_id,
                     session=None,manifest=None,clock_reports=None):
    """Verify stored bytes, exact host/key identity, signatures and complete shard coverage."""
    try:
        keys=_pinned_keys(pins)
        session_sha=None
        if session is not None:
            from host_session import validate
            session_sha=validate(session,manifest,pins)
        if clock_reports is not None and (session is None or len(clock_reports)!=len(report_paths)):
            raise ValueError('Signed clock reports require a complete session-bound set')
        if not _ID.fullmatch(expected_run_id):raise ValueError('Invalid run ID')
        if len(report_paths)!=len(shard_folders) or len(report_paths)<2 or len(report_paths)>256:
            raise ValueError('Wrong number of shard reports')
        from distributed_integrity import ordered_shard_folders
        shard_folders=ordered_shard_folders(shard_folders)
        # Files can arrive in any order; their *signed* shard indices determine
        # the mapping, not upload order or an attacker-controlled filename.
        indexed={}
        for path in report_paths:
            envelope=json.loads(_read_bounded(path,_MAX_REPORT))
            payload=envelope.get('payload') if isinstance(envelope,dict) else None
            shard=payload.get('shard_index') if isinstance(payload,dict) else None
            if type(shard) is not int or shard<0 or shard>=len(report_paths) or shard in indexed:
                raise ValueError('Missing, duplicate or malformed signed shard index')
            indexed[shard]=envelope
        if set(indexed)!=set(range(len(report_paths))):
            raise ValueError('Incomplete signed shard coverage')
        clock_by_shard={}
        if clock_reports is not None:
            for clock_path in clock_reports:
                raw_clock=_read_bounded(clock_path,512*1024)
                doc=json.loads(raw_clock)
                i=doc.get('shard_index') if isinstance(doc,dict) else None
                if (not isinstance(doc,dict) or doc.get('schema_version')!=1 or
                    doc.get('run_id')!=expected_run_id or type(i) is not int or
                    not 0<=i<len(report_paths) or i in clock_by_shard):
                    raise ValueError('Duplicate, missing or invalid signed clock shard')
                clock_by_shard[i]=_sha_bytes(raw_clock)
            if set(clock_by_shard)!=set(range(len(report_paths))):
                raise ValueError('Incomplete signed clock-report coverage')
        received=set();host_ids=set();reports=[]
        for i,folder in enumerate(shard_folders):
            envelope=indexed[i]
            if not isinstance(envelope,dict) or set(envelope)!={'payload','signature'}:
                raise ValueError('Malformed envelope')
            payload=envelope['payload'];signature=envelope['signature']
            required={'schema_version','run_id','host_id','shard_index','summary_sha256','agent_sha256'}
            if session is not None:required.add('session_sha256')
            if clock_reports is not None:required.add('clock_report_sha256')
            if not isinstance(payload,dict) or set(payload)!=required:
                raise ValueError('Malformed signed payload')
            if type(payload['schema_version']) is not int or payload['schema_version']!=1 or payload['run_id']!=expected_run_id:
                raise ValueError('Schema/run mismatch')
            host_id=payload['host_id'];i=payload['shard_index']
            if session is not None and (payload['session_sha256']!=session_sha or
                session['host_assignments'][i]!=host_id):
                raise ValueError('Wrong collector session or host/shard assignment')
            if clock_reports is not None and payload['clock_report_sha256']!=clock_by_shard[i]:
                raise ValueError('Signed clock evidence differs from collected bytes')
            if not isinstance(host_id,str) or host_id not in keys or type(i) is not int or i in received or host_id in host_ids:
                raise ValueError('Unknown, duplicated or mismatched host/shard identity')
            if not isinstance(signature,str) or len(signature)>128:
                raise ValueError('Invalid signature encoding')
            try:
                sig=base64.b64decode(signature,validate=True)
                keys[host_id].verify(sig,_NAMESPACE+_canonical(payload))
            except (InvalidSignature,ValueError,binascii.Error) as exc:
                raise ValueError('Signature invalid') from exc
            if len(sig)!=64:raise ValueError('Wrong signature length')
            actual=_folder_payload(folder,expected_run_id,host_id,i)
            if session is not None:actual['session_sha256']=session_sha
            if clock_reports is not None:actual['clock_report_sha256']=clock_by_shard[i]
            if actual!=payload:raise ValueError('Signed evidence differs from collected bytes')
            received.add(i);host_ids.add(host_id);reports.append({'shard':i,'host_id':host_id,'status':'SIGNED_KEY_PIN_VERIFIED_NOT_PHYSICAL_ATTESTATION'})
        if received!=set(range(len(report_paths))):raise ValueError('Missing shard signatures')
        return {'status':'ALL_PINNED_SIGNING_KEYS_VERIFIED_NOT_HOST_ATTESTATION','shards':sorted(reports,key=lambda x:x['shard'])},[]
    except (ValueError,TypeError,KeyError,OSError,UnicodeError,json.JSONDecodeError,binascii.Error) as exc:
        return None,[str(exc)]


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    s=sub.add_parser('sign');s.add_argument('--folder',required=True,type=pathlib.Path)
    s.add_argument('--run-id',required=True);s.add_argument('--host-id',required=True)
    s.add_argument('--shard',required=True,type=int);s.add_argument('--private-key',required=True,type=pathlib.Path)
    s.add_argument('--output',required=True,type=pathlib.Path)
    s.add_argument('--session',type=pathlib.Path)
    s.add_argument('--manifest',type=pathlib.Path)
    s.add_argument('--pins',type=pathlib.Path)
    s.add_argument('--clock-report',type=pathlib.Path)
    v=sub.add_parser('verify');v.add_argument('--pins',required=True,type=pathlib.Path)
    v.add_argument('--run-id',required=True);v.add_argument('--reports',required=True,nargs='+',type=pathlib.Path)
    v.add_argument('--folders',required=True,nargs='+',type=pathlib.Path)
    v.add_argument('--session',type=pathlib.Path)
    v.add_argument('--manifest',type=pathlib.Path)
    v.add_argument('--clock-reports',nargs='+',type=pathlib.Path)
    a=parser.parse_args(argv)
    try:
        if a.command=='sign':
            if a.output.exists():raise ValueError('Refusing to overwrite signed evidence')
            if a.session is not None and (a.manifest is None or a.pins is None):
                raise ValueError('Session signature requires --manifest and --pins')
            data=make_envelope(a.folder,a.run_id,a.host_id,a.shard,a.private_key,
                 session=__import__('host_session').read_json(a.session) if a.session else None,
                 manifest=__import__('host_session').read_json(a.manifest) if a.manifest else None,
                 pins=__import__('host_session').read_json(a.pins) if a.pins else None,
                 clock_report=a.clock_report)
            a.output.write_bytes(_canonical(data)+b'\n')
            print('HOST_EVIDENCE_SIGNED run='+a.run_id+' shard='+str(a.shard))
        else:
            pins=json.loads(_read_bounded(a.pins,128*1024))
            result,errors=verify_envelopes(pins,a.reports,a.folders,a.run_id,
                 session=__import__('host_session').read_json(a.session) if a.session else None,
                 manifest=__import__('host_session').read_json(a.manifest) if a.manifest else None,
                 clock_reports=a.clock_reports)
            if errors:raise ValueError('; '.join(errors))
            print(json.dumps(result,sort_keys=True))
        return 0
    except (ValueError,TypeError,KeyError,OSError,UnicodeError,json.JSONDecodeError) as exc:
        print('HOST_EVIDENCE_REJECTED',exc,file=sys.stderr)
        return 2

if __name__=='__main__':sys.exit(main())
