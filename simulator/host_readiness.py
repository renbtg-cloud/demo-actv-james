#!/usr/bin/env python3
"""Operator-initiated real Linux host preflight for distributed producer launches.

Checks launch-script provenance, Java/Python runtime, kernel identity, local
resources and optionally TCP/TLS reachability. Does NOT start traffic, handle
bearer credentials, claim physical host identity or make a production benchmark.
No network connections unless --check-network is explicitly requested.
"""
import argparse
import hashlib
import json
import os
import pathlib
import re
import socket
import ssl
import subprocess
import sys
import time
import urllib.parse
from host_provenance import snapshot
from host_session import read_json


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


def _dial(url, timeout):
    parsed=urllib.parse.urlsplit(url)
    host=parsed.hostname
    if (parsed.scheme not in ('https','http') or not host or parsed.username or parsed.password or
        parsed.path not in ('','/') or parsed.query or parsed.fragment):
        raise ValueError('Unsafe API URL for preflight')
    if parsed.scheme=='http' and host not in ('127.0.0.1','localhost','::1'):
        raise ValueError('Do not use plaintext off-loopback')
    port=parsed.port or (443 if parsed.scheme=='https' else 80)
    with socket.create_connection((host,port),timeout=timeout) as sock:
        if parsed.scheme=='https':
            with ssl.create_default_context().wrap_socket(sock,server_hostname=host) as secured:
                tls=secured.version()
                if not tls:raise ValueError('TLS handshake missing')
                return 'TLS_CERTIFICATE_VALIDATED'
        return 'LOOPBACK_TCP_CONNECTED_NO_APPLICATION_AUTH'


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
    if script.name!=expected or script.is_symlink() or not script.is_file() or script.stat().st_size>64*1024:
        raise ValueError('Missing or unsafe assigned launcher')
    digest=hashlib.sha256(script.read_bytes()).hexdigest()
    index=manifest.get('launch_sha256')
    if not isinstance(index,dict) or index.get(expected)!=digest:
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
    if not isinstance(target,str):raise ValueError('Missing target URL')
    parse=urllib.parse.urlsplit(target)
    if (parse.scheme not in ('http','https') or not parse.hostname or parse.username or
        parse.password or parse.query or parse.fragment or parse.path not in ('','/') or
        parse.scheme=='http' and parse.hostname not in ('127.0.0.1','localhost','::1')):
        raise ValueError('Unsafe API base URL')
    network='NOT_PROBED'
    if check_network:network=_dial(target,network_timeout)
    return {'schema_version':1,'run_id':manifest['run_id'],'shard_index':shard,
        'launch_script_sha256':digest,'java_major':java,
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
            with a.output.open('x',encoding='utf-8') as out:
                os.fchmod(out.fileno(),0o600)
                json.dump(result,out,sort_keys=True,indent=2);out.write('\n')
        print('HOST_PREFLIGHT_SELF_REPORTED_OK',json.dumps(result,sort_keys=True))
        return 0
    except (ValueError,OSError,TypeError,KeyError,subprocess.TimeoutExpired) as exc:
        print('HOST_PREFLIGHT_REJECTED',exc,file=sys.stderr)
        return 2

if __name__=='__main__':sys.exit(main())
