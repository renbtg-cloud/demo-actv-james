#!/usr/bin/env python3
"""Independent post-import verification of a previously published collection.

Replays Ed25519 signatures and trusted launch/session checks on the *published*
files, not merely on the ingress archives. If original transport ZIPs are
supplied, also re-checks their exact SHA-256 and byte sizes. The collection
index is not itself signed; this tool does not attest hosts or durable effects.
"""
import argparse
import hashlib
import json
import pathlib
import re
import stat
import sys
from distributed_integrity import verify_launch
from host_provenance import evaluate_provenance
from host_session import read_json, validate
from host_signed_evidence import verify_envelopes
from shard_bundle import inspect_archive, _MAX_ARCHIVE

_HEX=re.compile(r'[0-9a-f]{64}\Z')


def _hash(path):
    h=hashlib.sha256()
    with pathlib.Path(path).open('rb') as f:
        while block := f.read(65536):h.update(block)
    return h.hexdigest()


def _file(path,limit):
    st=path.lstat()
    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_size > limit:
        raise ValueError('Unsafe/oversized collected member: '+str(path))
    return st.st_size


def verify_collection(collection, pins, session, manifest, archives=None):
    path=pathlib.Path(collection)
    if path.is_symlink() or not path.is_dir():
        raise ValueError('Collected directory must be real')
    if isinstance(pins,(str,pathlib.Path)):pins=read_json(pins)
    if isinstance(session,(str,pathlib.Path)):session=read_json(session)
    if isinstance(manifest,(str,pathlib.Path)):manifest=read_json(manifest,2*1024*1024)
    session_sha=validate(session,manifest,pins)
    n=manifest['shards']
    if type(n) is not int or not 2<=n<=256:
        raise ValueError('Invalid shard count')
    expected={'collection.json'} | {f'shard-{i:03d}' for i in range(n)}
    if {p.name for p in path.iterdir()}!=expected:
        raise ValueError('Unexpected or missing collected top-level members')
    _file(path/'collection.json',2*1024*1024)
    report=read_json(path/'collection.json',2*1024*1024)
    required={'schema_version','run_id','session_sha256','signed_host_evidence',
              'host_evidence','bundles','scope'}
    if (not isinstance(report,dict) or set(report)!=required or
        type(report['schema_version']) is not int or report['schema_version']!=1 or
        report['run_id']!=manifest['run_id'] or report['session_sha256']!=session_sha or
        report['scope']!='SIGNED_TRANSPORT_IMPORT_NOT_GLOBAL_WORKLOAD_OR_PHYSICAL_HOST_PROOF'):
        raise ValueError('Collected report schema/session inconsistent')
    if not isinstance(report['bundles'],list) or len(report['bundles'])!=n:
        raise ValueError('Incomplete collection bundle inventory')
    summaries=[];folders=[];reports=[];clocks=[]
    for i in range(n):
        folder=path/f'shard-{i:03d}'
        if folder.is_symlink() or not folder.is_dir():
            raise ValueError('Unsafe collected shard directory')
        summary=folder/'summary.json'
        _file(summary,2*1024*1024)
        obj=read_json(summary,2*1024*1024)
        jvms=obj.get('plan',{}).get('jvms') if isinstance(obj,dict) else None
        if type(jvms) is not int or not 1<=jvms<=256:
            raise ValueError('Invalid collected JVM plan')
        members={'summary.json','signed-envelope.json'}|{f'agent-{j:03d}.tsv' for j in range(jvms)}
        names={p.name for p in folder.iterdir()}
        if 'clock-report.json' in names:members.add('clock-report.json')
        if members!=names:
            raise ValueError('Unexpected or missing collected evidence member')
        for member in members:
            limit=(128*1024*1024 if member.startswith('agent-') else
                   2*1024*1024 if member=='summary.json' else
                   512*1024 if member=='clock-report.json' else 64*1024)
            _file(folder/member,limit)
        folders.append(folder);summaries.append(obj)
        reports.append(folder/'signed-envelope.json')
        if 'clock-report.json' in members:clocks.append(folder/'clock-report.json')
        entry=report['bundles'][i]
        if (not isinstance(entry,dict) or set(entry)!={'shard','source_bundle_sha256',
            'source_bundle_bytes','members'} or type(entry['shard']) is not int or
            entry['shard']!=i or type(entry['source_bundle_bytes']) is not int or
            not 0<entry['source_bundle_bytes']<=_MAX_ARCHIVE or
            not isinstance(entry['source_bundle_sha256'],str) or
            not _HEX.fullmatch(entry['source_bundle_sha256']) or
            type(entry['members']) is not int or entry['members']!=len(members)):
            raise ValueError('Invalid collected bundle inventory')
    errs=verify_launch(manifest,summaries)
    if errs:raise ValueError('Published launch differs from trusted plan: '+'; '.join(errs))
    if clocks and len(clocks)!=n:raise ValueError('Partial collected clock evidence')
    signed,errs=verify_envelopes(pins,reports,folders,manifest['run_id'],
                     session=session,manifest=manifest,
                     clock_reports=clocks if clocks else None)
    if errs:raise ValueError('Published signature verification failed: '+'; '.join(errs))
    if signed!=report['signed_host_evidence']:
        raise ValueError('Claimed signing verdict differs from independent verification')
    hosts=evaluate_provenance([s.get('host_environment') for s in summaries],manifest['run_id'])
    if hosts!=report['host_evidence']:
        raise ValueError('Claimed host provenance differs from independently evaluated reports')
    if archives is not None:
        if len(archives)!=n:raise ValueError('Original bundle count mismatch')
        seen=set()
        for archive in archives:
            manifest_bundle=inspect_archive(archive)
            idx=manifest_bundle['shard_index']
            if idx in seen or not 0<=idx<n or manifest_bundle['run_id']!=manifest['run_id']:
                raise ValueError('Wrong/duplicated original bundle')
            seen.add(idx)
            entry=report['bundles'][idx]
            src=pathlib.Path(archive)
            if _file(src,_MAX_ARCHIVE)!=entry['source_bundle_bytes'] or _hash(src)!=entry['source_bundle_sha256']:
                raise ValueError('Original transport archive contradicts collected inventory')
        if seen!=set(range(n)):raise ValueError('Missing original bundle')
    return {'status':'REVERIFIED_SIGNED_COLLECTION_NOT_PHYSICAL_HOST_ATTESTATION',
            'run_id':manifest['run_id'],'shards':n,
            'original_bundles_checked':archives is not None}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--collection',required=True,type=pathlib.Path)
    p.add_argument('--pins',required=True,type=pathlib.Path)
    p.add_argument('--session',required=True,type=pathlib.Path)
    p.add_argument('--manifest',required=True,type=pathlib.Path)
    p.add_argument('--original-bundles',nargs='*',type=pathlib.Path)
    a=p.parse_args(argv)
    try:
        print(json.dumps(verify_collection(a.collection,a.pins,a.session,a.manifest,
                           a.original_bundles),sort_keys=True))
        return 0
    except (ValueError,TypeError,KeyError,OSError,json.JSONDecodeError,UnicodeError) as exc:
        print('COLLECTION_REVERIFICATION_REJECTED',exc,file=sys.stderr)
        return 2

if __name__=='__main__':sys.exit(main())
