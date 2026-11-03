#!/usr/bin/env python3
"""Fail-closed operator preflight inventory for 2..256 producer hosts.

Reports are UNTRUSTED SELF-REPORTS. Reject detectable same-kernel masquerading,
missing/wrong shard slots, changed launch plan, and unmet prerequisites; a PASS
only means apparent readiness, not authenticated remote identity or physical
independence. Subsequent signed shard evidence is mandatory.
"""
import argparse
import json
import os
import pathlib
import re
import stat
import secrets
import sys
import time
from host_session import read_json, digest
from host_provenance import evaluate_provenance

_HEX=re.compile(r'[0-9a-f]{64}\Z')
_REQUIRED={'schema_version','run_id','manifest_sha256','observed_epoch_ms','shard_index',
'launch_script_sha256','java_major','python_version','assigned_java_processes',
'cpu_affinity_count','reported_cgroup_memory_bytes','configured_java_heap_total_bytes',
'free_disk_bytes_at_check','kernel_fingerprint','network_evidence','claim'}
_NOTE='SELF_REPORTED_HOST_READINESS_NOT_IDENTITY_ATTESTATION_OR_PERFORMANCE_CERTIFICATION'
_NETWORK={'NOT_PROBED','TLS_CERTIFICATE_VALIDATED','LOOPBACK_TCP_CONNECTED_NO_APPLICATION_AUTH'}


def _reject_duplicate_keys(pairs):
    result={}
    for key,value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON object key in host readiness evidence')
        result[key]=value
    return result


def _read_host_report_bytes(path):
    # Open a bounded, regular file without following its final symlink. The
    # collector must separately protect the parent directory and source custody.
    flags=os.O_RDONLY | getattr(os,'O_CLOEXEC',0) | getattr(os,'O_NOFOLLOW',0) | getattr(os,'O_NONBLOCK',0)
    fd=os.open(path,flags)
    try:
        metadata=os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode) or not 0<metadata.st_size<=131072:
            raise ValueError('Unsafe or oversized host readiness report')
        with os.fdopen(os.dup(fd),'rb') as source:
            raw=source.read(131073)
        after=os.fstat(fd)
        if (len(raw)>131072 or len(raw)!=metadata.st_size or
            (metadata.st_dev,metadata.st_ino,metadata.st_size,metadata.st_mtime_ns,metadata.st_ctime_ns)!=
            (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns)):
            raise ValueError('Host readiness report changed or exceeded size limit')
        return raw
    finally:
        os.close(fd)


def _read_host_report(path):
    return json.loads(_read_host_report_bytes(path),object_pairs_hook=_reject_duplicate_keys,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Invalid JSON constant')))


def _check_temporal_evidence(manifest,indexed,*,now_ms,max_age_ms,max_future_ms):
    # These are plausibility checks over SELF-REPORTED clocks, not authenticated
    # synchronization or proof of arrival at the collector.
    if type(now_ms) is not int or now_ms<=0:
        raise ValueError('Invalid collector reference time')
    if type(max_age_ms) is not int or not 1000<=max_age_ms<=3600000:
        raise ValueError('Invalid readiness age budget')
    if type(max_future_ms) is not int or not 0<=max_future_ms<=60000:
        raise ValueError('Invalid readiness future skew budget')
    barrier=manifest.get('start_at_epoch_ms')
    plan=manifest.get('launch_plan')
    if (type(barrier) is not int or not isinstance(plan,dict) or
        type(plan.get('start_at_ms')) is not int or plan['start_at_ms']!=barrier):
        raise ValueError('Contradictory launch barrier in collector manifest')
    if barrier-now_ms<30000:
        raise ValueError('Launch barrier expired or less than 30 seconds ahead')
    for i,doc in indexed.items():
        sample=doc['observed_epoch_ms']
        if sample<now_ms-max_age_ms:
            raise ValueError(f'Host {i} readiness evidence too old')
        if sample>now_ms+max_future_ms:
            raise ValueError(f'Host {i} readiness evidence in implausible future')
        if sample>barrier-30000:
            raise ValueError(f'Host {i} readiness report too close to launch barrier')


def _publish_verdict(output,verdict):
    # Write and sync an owner-only temporary file. A same-directory hard link
    # atomically publishes the *complete* bytes without replacing an existing
    # result. A crash before linking leaves no falsely completed verdict.
    output=pathlib.Path(output)
    content=(json.dumps(verdict,sort_keys=True,indent=2,allow_nan=False)+'\n').encode('utf-8')
    temp=output.with_name('.'+output.name+'.'+secrets.token_hex(12)+'.tmp')
    flags=os.O_CREAT|os.O_EXCL|os.O_WRONLY|getattr(os,'O_CLOEXEC',0)|getattr(os,'O_NOFOLLOW',0)
    created=False
    try:
        fd=os.open(temp,flags,0o600)
        created=True
        try:
            with os.fdopen(fd,'wb') as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            raise
        os.link(temp,output,follow_symlinks=False)
        parent_fd=os.open(output.parent,os.O_RDONLY|getattr(os,'O_DIRECTORY',0))
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    finally:
        if created:temp.unlink(missing_ok=True)


def audit(manifest, reports, *, require_network=False, strict_temporal=False,
          now_ms=None, max_age_ms=300000, max_future_ms=10000,
          signed_readiness_paths=None, session=None, pins=None):
    if isinstance(manifest,(str,pathlib.Path)):manifest=read_json(manifest,2*1024*1024)
    if not isinstance(manifest,dict) or manifest.get('schema_version')!=2:
        raise ValueError('Expected trusted launch manifest v2')
    n=manifest.get('shards')
    if type(n) is not int or not 2<=n<=256 or len(reports)!=n:
        raise ValueError('Need exactly one report per assigned host')
    if type(require_network) is not bool or type(strict_temporal) is not bool:
        raise ValueError('Invalid readiness policy')
    if strict_temporal:
        if now_ms is None:now_ms=int(time.time()*1000)
        if type(max_age_ms) is not int or not 1000<=max_age_ms<=3600000:
            raise ValueError('Invalid readiness age budget')
        if type(max_future_ms) is not int or not 0<=max_future_ms<=60000:
            raise ValueError('Invalid readiness future skew budget')
    signed=signed_readiness_paths is not None or session is not None or pins is not None
    if signed and (signed_readiness_paths is None or session is None or pins is None):
        raise ValueError('Signed readiness requires complete session, pins and envelopes')
    indexed={}
    raw_by_shard={}
    for source in reports:
        if isinstance(source,(str,pathlib.Path)):
            raw=_read_host_report_bytes(source)
            doc=json.loads(raw,object_pairs_hook=_reject_duplicate_keys,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Invalid JSON constant')))
        else:
            if signed:raise ValueError('Signed readiness requires exact report files, not in-memory objects')
            raw=None
            doc=source
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
        if raw is not None:raw_by_shard[i]=raw
    if set(indexed)!=set(range(n)):
        raise ValueError('Missing host readiness shard')
    if strict_temporal:
        _check_temporal_evidence(manifest,indexed,now_ms=now_ms,
                                 max_age_ms=max_age_ms,max_future_ms=max_future_ms)
    # This rejects *observable* common-kernel collisions, but the self-reported
    # distinct fingerprint values are not authenticated hardware measurements.
    host_provenance=evaluate_provenance([{'schema_version':1,'kernel_fingerprint':indexed[i]['kernel_fingerprint']}
                                         for i in range(n)],manifest['run_id'])
    if host_provenance['status']!='DISTINCT_KERNEL_REPORTS_UNVERIFIED':
        raise ValueError('Independent host kernels not evidenced: '+host_provenance['status'])
    signed_status=None
    if signed:
        from host_readiness_signature import verify_proofs
        signed_status=verify_proofs(raw_by_shard,signed_readiness_paths,session,manifest,pins)
    return {'status':'APPARENT_READY_DISTINCT_KERNEL_REPORTS_UNVERIFIED',
            'run_id':manifest['run_id'],'shards':n,
            'network_required':require_network,
            'collector_checked_at_epoch_ms':now_ms if strict_temporal else None,
            'temporal_budgets':({'max_age_ms':max_age_ms,'max_future_ms':max_future_ms}
                                if strict_temporal else None),
            'manifest_sha256':digest(manifest),
            'signed_readiness':signed_status if signed else {'status':'NOT_REQUESTED'},
            'report_commitments':[
                {'shard_index':i,'canonical_sha256':digest(indexed[i])}
                for i in range(n)],
            'temporal_preflight':('BOUNDED_SELF_REPORTED_TIMES_UNVERIFIED'
                                  if strict_temporal else 'NOT_REQUESTED'),
            'network_probed_for_all':all(indexed[i]['network_evidence']!='NOT_PROBED' for i in indexed),
            'time_note':'Self-reported wall times are not cross-host comparable without separately audited clock bounds',
            'identity_note':'This report does not authenticate hosts or hardware; signed post-run evidence is required'}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',required=True,type=pathlib.Path)
    p.add_argument('--require-network',action='store_true')
    p.add_argument('--strict-temporal',action='store_true',
                   help='Reject stale, future, late-barrier or contradictory self-reported readiness')
    p.add_argument('--max-age-ms',type=int,default=300000)
    p.add_argument('--max-future-ms',type=int,default=10000)
    p.add_argument('--output',type=pathlib.Path)
    p.add_argument('--host-session',type=pathlib.Path)
    p.add_argument('--host-pins',type=pathlib.Path)
    p.add_argument('--signed-readiness',type=pathlib.Path,action='append')
    p.add_argument('reports',nargs='+',type=pathlib.Path)
    a=p.parse_args(argv)
    try:
        verdict=audit(a.manifest,a.reports,require_network=a.require_network,
                      strict_temporal=a.strict_temporal,max_age_ms=a.max_age_ms,
                      max_future_ms=a.max_future_ms,
                      signed_readiness_paths=a.signed_readiness,
                      session=read_json(a.host_session) if a.host_session else None,
                      pins=read_json(a.host_pins) if a.host_pins else None)
        if a.output is not None:
            _publish_verdict(a.output,verdict)
        print(json.dumps(verdict,sort_keys=True))
        return 0
    except (OSError,ValueError,KeyError,TypeError,json.JSONDecodeError) as exc:
        print('HOST_READINESS_MATRIX_REJECTED',exc,file=sys.stderr)
        return 2

if __name__=='__main__':sys.exit(main())
