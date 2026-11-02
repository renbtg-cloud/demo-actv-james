#!/usr/bin/env python3
"""Offline Linux collector: import every transported shard, verify signed roles.

Only trusted collector-held session, launch manifest and pinned public keys
establish the signing policy. This is NOT an authenticated transport, proof of
physical hosts, nor a global workload success verdict. All validation occurs
before an atomic Linux RENAME_NOREPLACE publishes the collection directory.
"""
import argparse
import ctypes
import errno
import hashlib
import json
import os
import pathlib
import shutil
import sys
import tempfile

from distributed_integrity import verify_launch
from host_provenance import evaluate_provenance
from host_session import read_json, validate, digest
from host_signed_evidence import verify_envelopes
from shard_bundle import inspect_archive, unpack


def _publish_no_replace(staged, destination):
    libc=ctypes.CDLL(None,use_errno=True)
    renameat2=getattr(libc,'renameat2',None)
    if renameat2 is None:
        raise ValueError('Linux renameat2 RENAME_NOREPLACE unavailable; refuse unsafe publication')
    renameat2.argtypes=(ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint)
    renameat2.restype=ctypes.c_int
    result=renameat2(-100,os.fsencode(staged),-100,os.fsencode(destination),1)
    if result != 0:
        error=ctypes.get_errno()
        raise OSError(error,os.strerror(error),str(destination))


def collect(archives, destination, pins, session, manifest, require_distinct_kernels=False):
    if isinstance(pins,(str,pathlib.Path)):pins=read_json(pins)
    if isinstance(session,(str,pathlib.Path)):session=read_json(session)
    if isinstance(manifest,(str,pathlib.Path)):manifest=read_json(manifest,2*1024*1024)
    session_hash=validate(session,manifest,pins)
    count=manifest['shards']
    if type(count) is not int or len(archives)!=count:
        raise ValueError('Need exactly one incoming archive for every assigned shard')
    target=pathlib.Path(destination)
    if (target.exists() or target.is_symlink() or target.parent.is_symlink() or
        not target.parent.is_dir()):
        raise ValueError('Destination must be an absent path in a trusted real directory')
    by_shard={}
    for path in archives:
        b=inspect_archive(path)
        i=b['shard_index']
        if (b['run_id']!=manifest['run_id'] or i>=count or i in by_shard):
            raise ValueError('Wrong run, duplicate or out-of-range incoming bundle')
        by_shard[i]=pathlib.Path(path)
    if set(by_shard)!=set(range(count)):
        raise ValueError('Incomplete incoming shard set')
    # Validation must complete before collecting files into a durable directory.
    stage=pathlib.Path(tempfile.mkdtemp(prefix='.deroyque-collect-',dir=target.parent))
    try:
        folders=[];reports=[];clocks=[];summaries=[];entries=[]
        for i in range(count):
            src=by_shard[i]
            folder=stage/f'shard-{i:03d}'
            evidence=unpack(src,folder)
            folders.append(folder)
            summaries.append(read_json(folder/'summary.json',2*1024*1024))
            reports.append(folder/'signed-envelope.json')
            if (folder/'clock-report.json').exists():clocks.append(folder/'clock-report.json')
            h=hashlib.sha256()
            with src.open('rb') as f:
                while b := f.read(65536):h.update(b)
            entries.append({'shard':i,'source_bundle_sha256':h.hexdigest(),
                            'members':len(evidence['members'])})
        launch_errors=verify_launch(manifest,summaries)
        if launch_errors:raise ValueError('Shards disagree with collector launch: '+'; '.join(launch_errors))
        host_evidence=evaluate_provenance([item.get('host_environment') for item in summaries], manifest['run_id'])
        if require_distinct_kernels and host_evidence['status'] != 'DISTINCT_KERNEL_REPORTS_UNVERIFIED':
            raise ValueError('Independent kernels not evidenced: '+host_evidence['status'])
        if clocks and len(clocks)!=count:
            raise ValueError('Partial clock evidence set cannot be accepted')
        signed,errors=verify_envelopes(pins,reports,folders,manifest['run_id'],
                                        session=session,manifest=manifest,
                                        clock_reports=clocks if clocks else None)
        if errors:raise ValueError('Pinned Ed25519 verification failed: '+'; '.join(errors))
        report={'schema_version':1,'run_id':manifest['run_id'],
                'session_sha256':session_hash,'signed_host_evidence':signed,
                'host_evidence':host_evidence,
                'bundles':entries,'scope':'SIGNED_TRANSPORT_IMPORT_NOT_GLOBAL_WORKLOAD_OR_PHYSICAL_HOST_PROOF'}
        with (stage/'collection.json').open('x',encoding='utf-8') as f:
            json.dump(report,f,sort_keys=True,indent=2)
            f.write('\n')
        _publish_no_replace(stage,target)
        return report
    except BaseException:
        shutil.rmtree(stage,ignore_errors=True)
        raise


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pins',required=True,type=pathlib.Path)
    parser.add_argument('--session',required=True,type=pathlib.Path)
    parser.add_argument('--manifest',required=True,type=pathlib.Path)
    parser.add_argument('--output-dir',required=True,type=pathlib.Path)
    parser.add_argument('--require-distinct-kernels',action='store_true',
                        help='Reject missing or same-kernel self-reports (NOT physical attestation)')
    parser.add_argument('bundles',nargs='+',type=pathlib.Path)
    args=parser.parse_args(argv)
    try:
        report=collect(args.bundles,args.output_dir,args.pins,args.session,args.manifest,
                       require_distinct_kernels=args.require_distinct_kernels)
        print(json.dumps({'status':report['scope'],'shards':len(report['bundles']),
                          'run_id':report['run_id']},sort_keys=True))
        return 0
    except (ValueError,TypeError,KeyError,OSError,json.JSONDecodeError) as exc:
        print('SIGNED_SHARD_COLLECTION_REJECTED',exc,file=sys.stderr)
        return 2

if __name__=='__main__':sys.exit(main())
