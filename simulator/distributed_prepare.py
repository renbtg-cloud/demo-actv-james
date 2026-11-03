#!/usr/bin/env python3
"""Generate safe, reproducible host-specific launch scripts for one shared workload.

Only prepares files; does NOT provision VMs, copy credentials, open public ports,
start remote processes or claim those hosts exist.
"""
import argparse
import datetime as dt
import hashlib
import json
import math
import ipaddress
import os
import pathlib
import shutil
import tempfile
import re
import shlex
import sys
import time
import uuid

from host_readiness import _parse_preflight_target
from atomic_publish import _publish_no_replace
from workload import validate_workload

def build_scripts(args,now_ms=None):
    # The generator and the host preflight must enforce the SAME URL contract.
    # Invalid manifest inputs must fail before scripts exist.
    try:
        target,host,_port=_parse_preflight_target(args.url)
    except ValueError as exc:
        if 'plaintext off-loopback' in str(exc):
            raise ValueError('Non-loopback HTTP would disclose bearer tokens: use HTTPS or an authenticated SSH tunnel to localhost') from exc
        raise
    try:
        literal=ipaddress.ip_address(host)
    except ValueError:
        literal=None
    local=host=='localhost' or (literal is not None and literal.is_loopback)
    validate_workload(args)  # Same workload invariants enforced by run.py.
    for field in ('rate','duration_seconds','ramp_start_rate','burst_rate',
                  'burst_width_seconds','burst_period_seconds','hotspot_percent',
                  'hotspot_keys_percent','skew_power'):
        value=getattr(args,field)
        if type(value) not in (int,float) or not math.isfinite(value):
            raise ValueError(f'{field} must be a finite workload number')
    if type(args.heap_max_mb) is not int or not 32<=args.heap_max_mb<=65536:
        raise ValueError('--heap-max-mb must be 32..65536')
    if type(args.concurrency) is not int or not 1<=args.concurrency<=10000:
        raise ValueError('--concurrency must be 1..10000')
    if type(args.start_after_seconds) is not int or not 30<=args.start_after_seconds<=86400:
        raise ValueError('Start delay must be between 30 and 86400 seconds')
    if args.shards<2 or args.shards>256:raise ValueError('--shards must be between 2 and 256')
    if args.requests<args.keys or args.keys<1:raise ValueError('requests >= keys >=1 required')
    if args.requests<args.shards:raise ValueError('Each shard must receive at least one POST')
    if not 0<=args.conflict_percent<=100:raise ValueError('conflict-percent must be 0..100')
    if args.rate<0:raise ValueError('rate must be nonnegative')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,48}',args.run_id):raise ValueError('invalid run_id')
    # Variable worker allocation is allowed; each machine has its own cgroup guard.
    counts=[int(part.strip()) for part in args.jvms_per_host.split(',')]
    if len(counts)==1:counts*=args.shards
    if len(counts)!=args.shards or any(x<1 or x>256 for x in counts):
        raise ValueError('Provide one JVM count or a comma-separated JVM count per host (1..256)')
    for index,n in enumerate(counts):
        if n>len(range(index,args.requests,args.shards)):
            raise ValueError(f'Host {index} has more JVMs than assigned POSTs')
    moment=now_ms if now_ms is not None else int(time.time()*1000)
    if type(moment) is not int or not 0<moment<253402200000000:
        raise ValueError('Invalid launch plan reference UTC time')
    start=moment+args.start_after_seconds*1000
    scripts={}
    for shard,jvms in enumerate(counts):
        argv=['python3','simulator/run.py','--url',args.url,'--shard-count',str(args.shards),
              '--shard-index',str(shard),'--jvms',str(jvms),
              '--requests',str(args.requests),'--keys',str(args.keys),
              '--rate',str(args.rate),'--profile',args.profile,
              '--distribution',args.distribution,'--conflict-percent',str(args.conflict_percent),
              '--seed',str(args.seed),'--concurrency',str(args.concurrency),
              '--heap-max-mb',str(args.heap_max_mb),
              '--start-at-ms',str(start),'--run-id',args.run_id,
              '--output-dir',f'simulator/results/{args.run_id}-host-{shard:03d}']
        if not local:argv.append('--allow-remote')
        if args.duration_seconds>0:argv.extend(['--duration-seconds',str(args.duration_seconds)])
        if args.profile=='ramp-up':argv.extend(['--ramp-start-rate',str(args.ramp_start_rate)])
        if args.profile=='burst':argv.extend(['--burst-rate',str(args.burst_rate),'--burst-width-seconds',str(args.burst_width_seconds),'--burst-period-seconds',str(args.burst_period_seconds)])
        if args.distribution=='hotspot':argv.extend(['--hotspot-percent',str(args.hotspot_percent),'--hotspot-keys-percent',str(args.hotspot_keys_percent)])
        if args.distribution=='skewed':argv.extend(['--skew-power',str(args.skew_power)])
        body=("#!/bin/sh\nset -eu\n"
              "# Copy the SAME Milestone 07 simulator to each host and run at its repository root.\n"
              "# Supply DEROYQUE_SIM_WRITE_TOKEN securely on this host (not in this file).\n"
              ": \"${DEROYQUE_SIM_WRITE_TOKEN:?Set DEROYQUE_SIM_WRITE_TOKEN securely}\"\n"
              "./simulator/build.sh\n"
              +shlex.join(argv)+"\n")
        scripts[f'launch-host-{shard:03d}.sh']=body
    plan={'profile':args.profile,'distribution':args.distribution,
          'duration_seconds':float(args.duration_seconds),'seed':args.seed,
          'global_attempted_posts':args.requests,'shared_keys':args.keys,
          'conflict_percent':args.conflict_percent,'target_qps':float(args.rate),
          'shard_count':args.shards,'start_at_ms':start,
          'ramp_start_rate':float(args.ramp_start_rate),'burst_rate':float(args.burst_rate),
          'burst_width_seconds':float(args.burst_width_seconds),
          'burst_period_seconds':float(args.burst_period_seconds),
          'hotspot_percent':float(args.hotspot_percent),
          'hotspot_keys_percent':float(args.hotspot_keys_percent),
          'skew_power':float(args.skew_power),
          'heap_max_mb':args.heap_max_mb,'concurrency_per_jvm':args.concurrency}
    manifest={'schema_version':2,'launch_plan':plan,'run_id':args.run_id,'target_url':args.url,'shards':args.shards,
              'jvms_per_host':counts,'global_posts':args.requests,'shared_keys':args.keys,
              'start_at_epoch_ms':start,
              'start_at_utc':dt.datetime.fromtimestamp(start/1000,dt.timezone.utc).isoformat(),
              'clock_requirement':'All hosts must run an independent clock-synchronization service; verify their UTC time before launch.',
              'cross_host_throughput':'NOT computed without verified clock offsets and a valid shared measurement window',
              'launch_sha256':{name:hashlib.sha256(src.encode()).hexdigest() for name,src in scripts.items()}}
    return scripts,manifest

def parse_args(argv=None):
    p=argparse.ArgumentParser(description='Prepare multi-host deterministic Deroyque input traffic')
    p.add_argument('--url',required=True,help='Same HTTPS URL on all hosts, or loopback address through a secure tunnel on each')
    p.add_argument('--shards',type=int,required=True);p.add_argument('--jvms-per-host',default='4')
    p.add_argument('--requests',type=int,required=True);p.add_argument('--keys',type=int,required=True)
    p.add_argument('--rate',type=float,default=60);p.add_argument('--profile',choices=('steady','ramp-up','burst','soak'),default='steady')
    p.add_argument('--distribution',choices=('sequential','hotspot','skewed','unique'),default='hotspot')
    p.add_argument('--conflict-percent',type=int,default=30);p.add_argument('--seed',type=int,default=1729)
    p.add_argument('--concurrency',type=int,default=8);p.add_argument('--heap-max-mb',type=int,default=32)
    p.add_argument('--duration-seconds',type=float,default=0);p.add_argument('--ramp-start-rate',type=float,default=1)
    p.add_argument('--burst-rate',type=float,default=100);p.add_argument('--burst-width-seconds',type=float,default=1);p.add_argument('--burst-period-seconds',type=float,default=5)
    p.add_argument('--hotspot-percent',type=float,default=80);p.add_argument('--hotspot-keys-percent',type=float,default=10);p.add_argument('--skew-power',type=float,default=2)
    p.add_argument('--start-after-seconds',type=int,default=90)
    p.add_argument('--run-id',default='dist-'+uuid.uuid4().hex[:12]);p.add_argument('--output-dir',type=pathlib.Path,required=True)
    return p.parse_args(argv)

def _publish_plan(destination, scripts, manifest):
    """Build a durable private complete staging tree, then publish no-replace.

    Local Linux atomic publication only. Directory owner/custody and physical
    host independence are out of scope; cleanup after power-loss is operational.
    """
    target=pathlib.Path(destination)
    parent=target.parent
    if not parent.is_dir() or parent.is_symlink() or target.exists() or target.is_symlink():
        raise ValueError('Plan destination must be absent in a trusted real directory')
    stage=pathlib.Path(tempfile.mkdtemp(prefix='.deroyque-plan-',dir=parent))
    try:
        os.chmod(stage,0o700)
        for name,body in scripts.items():
            if not re.fullmatch(r'launch-host-[0-9]{3}\.sh',name):
                raise ValueError('Invalid generated launcher path')
            file=stage/name
            fd=os.open(file,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o700)
            with os.fdopen(fd,'w',encoding='utf-8') as stream:
                os.fchmod(stream.fileno(),0o700)
                stream.write(body)
                stream.flush();os.fsync(stream.fileno())
        file=stage/'manifest.json'
        fd=os.open(file,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as stream:
            os.fchmod(stream.fileno(),0o600)
            json.dump(manifest,stream,sort_keys=True,indent=2,allow_nan=False)
            stream.write('\n');stream.flush();os.fsync(stream.fileno())
        folder_fd=os.open(stage,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:os.fsync(folder_fd)
        finally:os.close(folder_fd)
        _publish_no_replace(stage,target)
        parent_fd=os.open(parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:os.fsync(parent_fd)
        finally:os.close(parent_fd)
    finally:
        # On successful rename, staging path no longer exists; on failure,
        # discard incomplete private staging rather than a partial plan.
        if stage.exists():shutil.rmtree(stage)


def main(argv=None):
    a=parse_args(argv)
    try:
        scripts,manifest=build_scripts(a)
        _publish_plan(a.output_dir,scripts,manifest)
        print(f'PREPARED {len(scripts)} host launch scripts: {a.output_dir}; UTC barrier {manifest["start_at_utc"]}')
        print('Merge after transferring all host result directories:')
        print('python3 simulator/distributed_merge.py --verify-state '+
              ' '.join(f'simulator/results/{a.run_id}-host-{i:03d}' for i in range(a.shards)))
        return 0
    except (ValueError,OSError,TypeError,OverflowError) as e:
        print(f'DISTRIBUTED_PREPARE_FATAL {e}',file=sys.stderr);return 2

if __name__=='__main__':sys.exit(main())
