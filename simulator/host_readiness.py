#!/usr/bin/env python3
"""Operator-initiated real Linux host preflight for distributed producer launches.

Checks launch-script provenance, Java/Python runtime, kernel identity, local
resources and optionally TCP/TLS reachability. Does NOT start traffic, handle
bearer credentials, claim physical host identity or make a production benchmark.
No network connections unless --check-network is explicitly requested.
"""
import argparse
import hashlib
import ipaddress
import json
import os
import pathlib
import re
import socket
import stat
import ssl
import subprocess
import sys
import time
import urllib.parse
from host_provenance import snapshot
from host_session import read_json, digest


_MAX_LAUNCHER_BYTES=64*1024


def _read_launcher_snapshot(path):
    """Read one bounded regular launcher via a pinned descriptor, without symlinks.

    Detect obvious concurrent rewrites. This is *not* filesystem-level
    immutability, trusted host identity, or a guarantee against an attacker
    controlling the parent directory. The launcher must stay operator-controlled.
    """
    flags=(os.O_RDONLY | getattr(os,'O_NOFOLLOW',0) |
           getattr(os,'O_NONBLOCK',0) | getattr(os,'O_CLOEXEC',0))
    fd=os.open(path,flags)
    try:
        before=os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or not 0<before.st_size<=_MAX_LAUNCHER_BYTES:
            raise ValueError('Unsafe or oversized assigned launcher')
        with os.fdopen(os.dup(fd),'rb') as source:
            contents=source.read(_MAX_LAUNCHER_BYTES+1)
        after=os.fstat(fd)
        identity=lambda item: (item.st_dev,item.st_ino,item.st_size,item.st_mtime_ns,item.st_ctime_ns)
        if len(contents)!=before.st_size or len(contents)>_MAX_LAUNCHER_BYTES or identity(before)!=identity(after):
            raise ValueError('Assigned launcher changed during snapshot')
        return contents
    finally:
        os.close(fd)


def _java_version(binary='java'):
    result=subprocess.run([binary,'-version'],capture_output=True,text=True,timeout=5)
    if result.returncode:
        raise ValueError('Java runtime version query failed')
    found=re.search(r'\bversion\s+"((?:1\.)?\d+)',result.stdout+'\n'+result.stderr)
    if not found:
        found=re.search(r'\bopenjdk\s+((?:1\.)?\d+)',result.stdout+'\n'+result.stderr, re.I)
    if not found:raise ValueError('Unrecognized Java runtime version')
    major=int(found.group(1).split('.')[-1])
    if major<21:raise ValueError('Requires Java 21 or newer')
    return major


def _parse_preflight_target(url):
    """Reject ambiguous remote endpoints before any network side effect.

    This is a TLS/loopback *setup* check, never API authentication or host
    ownership verification. Plaintext outside literal loopback/localhost fails.
    """
    if not isinstance(url,str):
        raise ValueError('Missing target URL')
    try:
        parsed=urllib.parse.urlsplit(url)
        host=parsed.hostname
        port=parsed.port  # Force validation of malformed/out-of-range ports.
    except ValueError as exc:
        raise ValueError('Malformed preflight target URL') from exc
    if (parsed.scheme not in ('https','http') or not host or
        parsed.username or parsed.password or parsed.path not in ('','/') or
        parsed.query or parsed.fragment or not parsed.netloc or
        '@' in parsed.netloc or '%' in parsed.netloc or
        any(c.isspace() for c in url)):
        raise ValueError('Unsafe API URL for preflight')
    try:
        addr=ipaddress.ip_address(host)
    except ValueError:
        addr=None
    if parsed.scheme=='http' and not ((addr is not None and addr.is_loopback) or host=='localhost'):
        raise ValueError('Do not use plaintext off-loopback')
    if port==0:
        raise ValueError('Invalid preflight target port')
    return parsed,host,port if port is not None else (443 if parsed.scheme=='https' else 80)


def _dial(url,timeout):
    parsed,host,port=_parse_preflight_target(url)
    if parsed.scheme=='http':
        # Do not trust local aliases to resolve only to loopback without
        # checking *every* result; connect to the pinned numeric result rather
        # than doing a second, potentially different DNS resolution.
        candidates=socket.getaddrinfo(host,port,type=socket.SOCK_STREAM)
        if not candidates or any(not ipaddress.ip_address(c[4][0]).is_loopback for c in candidates):
            raise ValueError('Plaintext target resolved outside loopback')
        # socket.create_connection expects (host,port), and using a numeric
        # loopback address prevents rebinding between lookup and connection.
        selected=candidates[0][4][0]
        with socket.create_connection((selected,port),timeout=timeout):
            return 'LOOPBACK_TCP_CONNECTED_NO_APPLICATION_AUTH'
    with socket.create_connection((host,port),timeout=timeout) as sock:
        with ssl.create_default_context().wrap_socket(sock,server_hostname=host) as secured:
            if not secured.version():
                raise ValueError('TLS handshake missing')
            return 'TLS_CERTIFICATE_VALIDATED'


def check(manifest, shard, launcher, *, now_ms=None, min_free_mib=128,
          check_network=False, network_timeout=2.0, java_binary='java'):
    if isinstance(manifest,(str,pathlib.Path)):manifest=read_json(manifest,2*1024*1024)
    if not isinstance(manifest,dict) or manifest.get('schema_version')!=2:
        raise ValueError('Expected launch manifest v2')
    n=manifest.get('shards')
    if type(n) is not int or not 2<=n<=256 or type(shard) is not int or not 0<=shard<n:
        raise ValueError('Invalid host shard assignment')
    if type(min_free_mib) is not int or not 16<=min_free_mib<=32768:
        raise ValueError('Invalid free-space threshold')
    if type(network_timeout) not in (int,float) or not .1<=network_timeout<=10:
        raise ValueError('Invalid network timeout')
    if sys.version_info < (3,12):raise ValueError('Requires Python 3.12 or newer')
    script=pathlib.Path(launcher)
    expected=f'launch-host-{shard:03d}.sh'
    if script.name!=expected:
        raise ValueError('Missing or unsafe assigned launcher')
    from launch_contract import replay_manifest
    canonical=replay_manifest(manifest)
    actual_launcher=_read_launcher_snapshot(script)
    launcher_digest=hashlib.sha256(actual_launcher).hexdigest()
    index=manifest['launch_sha256']
    if index[expected]!=launcher_digest or actual_launcher!=canonical[expected]:
        raise ValueError('Assigned launcher differs from collector plan')
    count=manifest.get('jvms_per_host')
    if not isinstance(count,list) or len(count)!=n or type(count[shard]) is not int or not 1<=count[shard]<=256:
        raise ValueError('Invalid JVM assignment')
    bar=manifest.get('start_at_epoch_ms')
    when=int(time.time()*1000) if now_ms is None else now_ms
    if type(bar) is not int or type(when) is not int or bar-when<30_000:
        raise ValueError('Launch barrier must be at least 30 seconds in the future')
    environment=snapshot(manifest['run_id'])
    if not environment['kernel_fingerprint']:
        raise ValueError('Linux boot identity unavailable (cannot compare kernels)')
    java=_java_version(java_binary)
    affinity=environment['cpu_affinity_count']
    if type(affinity) is not int or affinity<1:
        raise ValueError('Missing CPU affinity evidence')
    root=script.parent
    stats=os.statvfs(root)
    free=stats.f_bavail*stats.f_frsize
    if free<min_free_mib*1024*1024:
        raise ValueError('Insufficient disk space for evidence results')
    memory=environment.get('cgroup_memory_limit')
    try:
        memory_bytes=int(memory) if memory not in ('max',None) else None
    except ValueError:
        memory_bytes=None
    heap=manifest.get('launch_plan',{}).get('heap_max_mb')
    if type(heap) is not int or not 1<=heap<=65536:
        raise ValueError('Invalid heap plan')
    # NOT a memory sufficiency guarantee: virtual threads, JVM/native memory,
    # kernel pressure, burst behavior and provider operations remain unmeasured.
    configured_heap_bytes=count[shard]*heap*1024*1024
    if memory_bytes is not None and configured_heap_bytes>memory_bytes:
        raise ValueError('Sum of configured JVM heaps exceeds cgroup memory limit')
    target=manifest.get('target_url')
    _parse_preflight_target(target)
    network='NOT_PROBED'
    if check_network:network=_dial(target,network_timeout)
    return {'schema_version':2,'run_id':manifest['run_id'],
        'manifest_sha256':digest(manifest), 'observed_epoch_ms':when, 'shard_index':shard,
        'launch_script_sha256':launcher_digest,'java_major':java,
        'python_version':'.'.join(map(str,sys.version_info[:3])),
        'assigned_java_processes':count[shard],
        'cpu_affinity_count':affinity,'reported_cgroup_memory_bytes':memory_bytes,
        'configured_java_heap_total_bytes':configured_heap_bytes,
        'free_disk_bytes_at_check':free,'kernel_fingerprint':environment['kernel_fingerprint'],
        'network_evidence':network,
        'claim':'SELF_REPORTED_HOST_READINESS_NOT_IDENTITY_ATTESTATION_OR_PERFORMANCE_CERTIFICATION'}


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest',required=True,type=pathlib.Path)
    p.add_argument('--shard-index',required=True,type=int)
    p.add_argument('--launcher',required=True,type=pathlib.Path)
    p.add_argument('--output',type=pathlib.Path)
    p.add_argument('--min-free-mib',type=int,default=128)
    p.add_argument('--check-network',action='store_true')
    p.add_argument('--network-timeout',type=float,default=2.0)
    a=p.parse_args(argv)
    try:
        result=check(a.manifest,a.shard_index,a.launcher,
                     min_free_mib=a.min_free_mib,check_network=a.check_network,
                     network_timeout=a.network_timeout)
        if a.output is not None:
            # Same atomic owner-only, no-overwrite publication contract as the
            # independent collector verdict. No world-readable creation window.
            from host_matrix import _publish_verdict
            _publish_verdict(a.output,result)
        print('HOST_PREFLIGHT_SELF_REPORTED_OK',json.dumps(result,sort_keys=True))
        return 0
    except (ValueError,OSError,TypeError,KeyError,subprocess.TimeoutExpired) as exc:
        print('HOST_PREFLIGHT_REJECTED',exc,file=sys.stderr)
        return 2

if __name__=='__main__':sys.exit(main())
