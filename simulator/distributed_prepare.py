#!/usr/bin/env python3
"""Generate safe, reproducible host-specific launch scripts for one shared workload.

Only prepares files; does NOT provision VMs, copy credentials, open public ports,
start remote processes or claim those hosts exist.
"""
import argparse
import datetime as dt
import hashlib
import json
import pathlib
import re
import shlex
import sys
import time
import urllib.parse
import uuid

def build_scripts(args,now_ms=None):
    target=urllib.parse.urlsplit(args.url)
    if target.scheme not in ('http','https') or not target.hostname or target.username or target.password or target.path not in ('','/') or target.query or target.fragment:
        raise ValueError('Expected plain http(s) API base URL without credentials/path/query')
    local=target.hostname in ('127.0.0.1','localhost','::1')
    if not local and target.scheme!='https':
        raise ValueError('Non-loopback HTTP would disclose bearer tokens: use HTTPS or an authenticated SSH tunnel to localhost')
    if args.shards<2 or args.shards>256:raise ValueError('--shards must be between 2 and 256')
    if args.requests<args.keys or args.keys<1:raise ValueError('requests >= keys >=1 required')
    if args.requests<args.shards:raise ValueError('Each shard must receive at least one POST')
    if args.start_after_seconds<30:raise ValueError('Start delay >=30 seconds required for remote launch coordination')
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
    start=moment+int(args.start_after_seconds*1000)
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

def main(argv=None):
    a=parse_args(argv)
    try:
        scripts,manifest=build_scripts(a)
        a.output_dir.mkdir(parents=True,exist_ok=False)
        for name,body in scripts.items():
            file=a.output_dir/name;file.write_text(body);file.chmod(0o750)
        (a.output_dir/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
        print(f'PREPARED {len(scripts)} host launch scripts: {a.output_dir}; UTC barrier {manifest["start_at_utc"]}')
        print('Merge after transferring all host result directories:')
        print('python3 simulator/distributed_merge.py --verify-state '+
              ' '.join(f'simulator/results/{a.run_id}-host-{i:03d}' for i in range(a.shards)))
        return 0
    except (ValueError,OSError) as e:
        print(f'DISTRIBUTED_PREPARE_FATAL {e}',file=sys.stderr);return 2

if __name__=='__main__':sys.exit(main())
