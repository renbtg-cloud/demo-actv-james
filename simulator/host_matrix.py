#!/usr/bin/env python3
"""Fail-closed operator preflight inventory for 2..256 producer hosts.

Reports are UNTRUSTED SELF-REPORTS. Reject detectable same-kernel masquerading,
missing/wrong shard slots, changed launch plan, and unmet prerequisites; a PASS
only means apparent readiness, not authenticated remote identity or physical
independence. Subsequent signed shard evidence is mandatory.
"""
import argparse
import json
import pathlib
import re
import sys
from host_session import read_json, digest
from host_provenance import evaluate_provenance

_HEX=re.compile(r'[0-9a-f]{64}\Z')
_REQUIRED={'schema_version','run_id','manifest_sha256','observed_epoch_ms','shard_index',
'launch_script_sha256','java_major','python_version','assigned_java_processes',
'cpu_affinity_count','reported_cgroup_memory_bytes','configured_java_heap_total_bytes',
'free_disk_bytes_at_check','kernel_fingerprint','network_evidence','claim'}
_NOTE='SELF_REPORTED_HOST_READINESS_NOT_IDENTITY_ATTESTATION_OR_PERFORMANCE_CERTIFICATION'
_NETWORK={'NOT_PROBED','TLS_CERTIFICATE_VALIDATED','LOOPBACK_TCP_CONNECTED_NO_APPLICATION_AUTH'}


def audit(manifest, reports, *, require_network=False):
    if isinstance(manifest,(str,pathlib.Path)):manifest=read_json(manifest,2*1024*1024)
    if not isinstance(manifest,dict) or manifest.get('schema_version')!=2:
        raise ValueError('Expected trusted launch manifest v2')
    n=manifest.get('shards')
    if type(n) is not int or not 2<=n<=256 or len(reports)!=n:
        raise ValueError('Need exactly one report per assigned host')
    if type(require_network) is not bool:raise ValueError('Invalid network policy')
    indexed={}
    for source in reports:
        doc=read_json(source,131072) if isinstance(source,(str,pathlib.Path)) else source
        if not isinstance(doc,dict) or set(doc)!=_REQUIRED or type(doc['schema_version']) is not int or doc['schema_version']!=2:
            raise ValueError('Malformed host readiness report')
        i=doc['shard_index']
        if type(i) is not int or not 0<=i<n or i in indexed:
            raise ValueError('Duplicate or invalid host shard index')
        if (doc['run_id']!=manifest['run_id'] or doc['manifest_sha256']!=digest(manifest) or
            doc['claim']!=_NOTE or doc['launch_script_sha256']!=
            manifest.get('launch_sha256',{}).get(f'launch-host-{i:03d}.sh')):
            raise ValueError('Host readiness differs from trusted manifest')
        if (type(doc['java_major']) is not int or doc['java_major']<21 or
            not isinstance(doc['python_version'],str) or
            not re.fullmatch(r'3\.(1[2-9]|[2-9][0-9])\.[0-9]+',doc['python_version']) or
            type(doc['assigned_java_processes']) is not int or
            doc['assigned_java_processes']!=manifest['jvms_per_host'][i] or
            type(doc['cpu_affinity_count']) is not int or doc['cpu_affinity_count']<1 or
            type(doc['configured_java_heap_total_bytes']) is not int or
            doc['configured_java_heap_total_bytes']!=
               manifest['jvms_per_host'][i]*manifest['launch_plan']['heap_max_mb']*1024*1024 or
            type(doc['free_disk_bytes_at_check']) is not int or doc['free_disk_bytes_at_check']<128*1024*1024 or
            type(doc['observed_epoch_ms']) is not int or doc['observed_epoch_ms']<=0 or
            doc['network_evidence'] not in _NETWORK):
            raise ValueError('Host runtime or resource evidence contradicts required preflight')
        mem=doc['reported_cgroup_memory_bytes']
        if (mem is not None and (type(mem) is not int or mem<doc['configured_java_heap_total_bytes'])):
            raise ValueError('Reported memory cgroup cannot fit configured heaps')
        if (not isinstance(doc['kernel_fingerprint'],str) or not _HEX.fullmatch(doc['kernel_fingerprint'])):
            raise ValueError('Missing/invalid kernel fingerprint')
        if require_network and doc['network_evidence']=='NOT_PROBED':
            raise ValueError('Required TCP/TLS probe was not performed')
        indexed[i]=doc
    if set(indexed)!=set(range(n)):
        raise ValueError('Missing host readiness shard')
    # This rejects *observable* common-kernel collisions, but the self-reported
    # distinct fingerprint values are not authenticated hardware measurements.
    host_provenance=evaluate_provenance([{'schema_version':1,'kernel_fingerprint':indexed[i]['kernel_fingerprint']}
                                         for i in range(n)],manifest['run_id'])
    if host_provenance['status']!='DISTINCT_KERNEL_REPORTS_UNVERIFIED':
        raise ValueError('Independent host kernels not evidenced: '+host_provenance['status'])
    return {'status':'APPARENT_READY_DISTINCT_KERNEL_REPORTS_UNVERIFIED',
            'run_id':manifest['run_id'],'shards':n,
            'network_probed_for_all':all(indexed[i]['network_evidence']!='NOT_PROBED' for i in indexed),
            'time_note':'Self-reported wall times are not cross-host comparable without separately audited clock bounds',
            'identity_note':'This report does not authenticate hosts or hardware; signed post-run evidence is required'}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',required=True,type=pathlib.Path)
    p.add_argument('--require-network',action='store_true')
    p.add_argument('--output',type=pathlib.Path)
    p.add_argument('reports',nargs='+',type=pathlib.Path)
    a=p.parse_args(argv)
    try:
        verdict=audit(a.manifest,a.reports,require_network=a.require_network)
        if a.output is not None:
            with a.output.open('x',encoding='utf-8') as stream:
                json.dump(verdict,stream,sort_keys=True,indent=2);stream.write('\n')
        print(json.dumps(verdict,sort_keys=True))
        return 0
    except (OSError,ValueError,KeyError,TypeError,json.JSONDecodeError) as exc:
        print('HOST_READINESS_MATRIX_REJECTED',exc,file=sys.stderr)
        return 2

if __name__=='__main__':sys.exit(main())
