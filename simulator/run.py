#!/usr/bin/env python3
"""Multi-JVM admission load generator for the *actual* Deroyque public API.

Only starts its own Java producers. The application and provider must already run.
Default target safety restricts the test to loopback hosts. Does not store secrets.
"""
import argparse
import collections
import csv
import json
import math
import os
import pathlib
import random
import re
import statistics
import subprocess
import sys
import time
import uuid
import urllib.parse
import urllib.request
import urllib.error
import concurrent.futures
import shutil
from workload import create_plan, validate_workload, schedule_seconds
from cpu_placement import parse_cpus,format_cpus

ROOT=pathlib.Path(__file__).resolve().parent

def positive_int(x):
    n=int(x)
    if n<1:raise argparse.ArgumentTypeError('Must be positive')
    return n

def pct(x):
    n=int(x)
    if not 0<=n<=100:raise argparse.ArgumentTypeError('Percent must be 0..100')
    return n

# YAML configuration is opt-in; CLI values override YAML, which overrides defaults.
DEFAULTS = dict(url='http://127.0.0.1:18081',allow_remote=False,jvms=4,concurrency=12,
    requests=200,keys=100,conflict_percent=20,rate=0.0,timeout_ms=8000,start_delay_ms=6000,
    max_error_percent=0.0,verify_state=False,await_done_seconds=0.0,run_id=None,
    output_dir=None,java='java',profile='steady',duration_seconds=0.0,ramp_start_rate=1.0,
    burst_rate=100.0,burst_width_seconds=1.0,burst_period_seconds=5.0,
    distribution='sequential',hotspot_percent=80.0,hotspot_keys_percent=10.0,
    skew_power=2.0,seed=1729,auto_jvms=False,auto_max_jvms=256,heap_min_mb=16,heap_max_mb=64,active_processors=2,
    unsafe_resource_override=False,producer_cpus=None,shard_index=0,shard_count=1,start_at_ms=None)

def parse_args(argv=None):
    p=argparse.ArgumentParser(description='Multi-JVM configurable Deroyque ingress load generator',argument_default=argparse.SUPPRESS)
    p.add_argument('--shard-count',type=positive_int,help='number of independently executed host shards')
    p.add_argument('--shard-index',type=int,help='zero-based shard index for this host')
    p.add_argument('--start-at-ms',type=int,help='shared absolute UTC epoch-millisecond start barrier')
    p.add_argument('--producer-cpus',help='CPU affinity list inherited by every Java producer (e.g. 0,1); placement is NOT reserved cores')
    p.add_argument('--config',type=pathlib.Path,help='YAML configuration (safe_load; no tokens)')
    p.add_argument('--url');p.add_argument('--allow-remote',action='store_true',default=argparse.SUPPRESS)
    p.add_argument('--jvms',type=positive_int);p.add_argument('--concurrency',type=positive_int)
    p.add_argument('--requests',type=positive_int);p.add_argument('--keys',type=positive_int)
    p.add_argument('--conflict-percent',type=pct);p.add_argument('--rate',type=float)
    p.add_argument('--timeout-ms',type=positive_int);p.add_argument('--start-delay-ms',type=int)
    p.add_argument('--max-error-percent',type=float)
    p.add_argument('--verify-state',action='store_true',default=argparse.SUPPRESS)
    p.add_argument('--await-done-seconds',type=float);p.add_argument('--run-id')
    p.add_argument('--output-dir',type=pathlib.Path);p.add_argument('--java')
    p.add_argument('--profile',choices=('steady','ramp-up','burst','soak'))
    p.add_argument('--duration-seconds',type=float);p.add_argument('--ramp-start-rate',type=float)
    p.add_argument('--burst-rate',type=float);p.add_argument('--burst-width-seconds',type=float)
    p.add_argument('--burst-period-seconds',type=float)
    p.add_argument('--distribution',choices=('sequential','hotspot','skewed','unique'))
    p.add_argument('--hotspot-percent',type=float);p.add_argument('--hotspot-keys-percent',type=float)
    p.add_argument('--skew-power',type=float);p.add_argument('--seed',type=int)
    p.add_argument('--auto-jvms',action='store_true',default=argparse.SUPPRESS,
                   help='choose maximum JVM count within memory guard and --auto-max-jvms')
    p.add_argument('--auto-max-jvms',type=positive_int)
    p.add_argument('--heap-min-mb',type=positive_int);p.add_argument('--heap-max-mb',type=positive_int)
    p.add_argument('--active-processors',type=positive_int)
    p.add_argument('--unsafe-resource-override',action='store_true',default=argparse.SUPPRESS,
                   help='override estimated process memory guard; only on authorized test hardware')
    explicit=vars(p.parse_args(argv))
    yaml_values={}
    if explicit.get('config'):
        try:import yaml
        except ImportError as e:raise ValueError('YAML requires PyYAML: pip install PyYAML') from e
        parsed=yaml.safe_load(explicit['config'].read_text(encoding='utf-8'))
        if not isinstance(parsed,dict):raise ValueError('YAML root must be a mapping')
        for key,value in parsed.items():
            name=key.replace('-','_')
            if name not in DEFAULTS:raise ValueError(f'Unknown YAML setting {key!r}')
            if name in ('run_id','output_dir','java','url','profile','distribution','producer_cpus') and value is not None:
                if not isinstance(value,str):raise ValueError(f'{name} must be a string')
            elif name in ('allow_remote','verify_state','unsafe_resource_override','auto_jvms'):
                if type(value) is not bool:raise ValueError(f'{name} must be a YAML boolean')
            elif name in ('jvms','concurrency','requests','keys','conflict_percent','timeout_ms',
                    'start_delay_ms','seed','shard_count','shard_index','start_at_ms','heap_min_mb','heap_max_mb','active_processors','auto_max_jvms'):
                if type(value) is not int:raise ValueError(f'{name} must be an integer')
            elif type(value) not in (int,float):raise ValueError(f'{name} must be numeric')
            yaml_values[name]=value
    explicit.pop('config',None)
    result={**DEFAULTS,**yaml_values,**explicit}
    if result['output_dir'] is not None:result['output_dir']=pathlib.Path(result['output_dir'])
    return argparse.Namespace(**result)

def duration_ms_to_percentile(values,percentile):
    if not values:return None
    pos=math.ceil(percentile/100*len(values))-1
    return round(sorted(values)[max(0,pos)]/1e6,3)

def validate_target(args):
    u=urllib.parse.urlsplit(args.url)
    if u.scheme not in ('http','https') or not u.hostname or u.username or u.password or u.query or u.fragment or u.path not in ('','/'):
        raise ValueError('Supply a plain http(s) base URL without path, credentials, query or fragment')
    if u.hostname not in ('127.0.0.1','localhost','::1') and not args.allow_remote:
        raise ValueError('Non-loopback target requires --allow-remote')
    if args.producer_cpus is not None:parse_cpus(args.producer_cpus)
    if args.rate<0 or not math.isfinite(args.rate):raise ValueError('--rate must be finite and nonnegative')
    if args.start_delay_ms<0:raise ValueError('--start-delay-ms must be >=0')
    if args.await_done_seconds<0:raise ValueError('--await-done-seconds must be >=0')
    if not 0<=args.max_error_percent<=100:raise ValueError('--max-error-percent must be between 0 and 100')
    if args.shard_count<1 or not (0<=args.shard_index<args.shard_count):raise ValueError('shard index out of range')
    local_requests=len(range(args.shard_index,args.requests,args.shard_count))
    if local_requests<1:raise ValueError('shard contains no requests')
    if args.jvms>local_requests:raise ValueError('Number of JVMs cannot exceed requests assigned to this shard')
    if args.start_at_ms is not None and (args.start_at_ms<0 or args.start_at_ms>253402300799000):raise ValueError('invalid absolute UTC start time')
    if args.shard_count>1 and args.verify_state:raise ValueError('Use --verify-state on distributed_merge.py after combining all shards')
    if args.jvms>256:raise ValueError('More than 256 JVMs requires changing explicit safety guard in run.py')
    if args.run_id and not re.fullmatch(r'[A-Za-z0-9_-]{1,48}',args.run_id):raise ValueError('Invalid --run-id')
    if args.keys>1000000:raise ValueError('--keys >1,000,000 not supported')
    if args.heap_min_mb>args.heap_max_mb:raise ValueError('heap min > max')
    if args.heap_max_mb<32:raise ValueError('heap max must be >=32 MB')
    validate_workload(args)
    # 100-char field on server; key prefix plus '-' and decimal number.
    if len(args.run_id or '')+1+len(str(args.keys-1))>100:raise ValueError('externalId too long')

def detect_cgroup_memory():
    def numeric(name):
        try:
            content=(pathlib.Path('/sys/fs/cgroup')/name).read_text().strip()
            return None if content=='max' else int(content)
        except (OSError,ValueError):return None
    return numeric('memory.max'),numeric('memory.current')

def choose_jvms(args):
    cap,used=detect_cgroup_memory()
    if cap is None or used is None:
        raise ValueError('--auto-jvms requires cgroup v2 memory.max and memory.current; use explicit --jvms otherwise')
    available=max(0,cap-used)
    per_process=(args.heap_max_mb+48)*1024*1024
    count=min(len(range(args.shard_index,args.requests,args.shard_count)),args.auto_max_jvms,256,int(available*.75//per_process))
    if count<1:raise ValueError('Insufficient cgroup memory headroom for even one JVM')
    return count

def resource_report(args):
    # cgroup v2 if available. JVM native memory, direct buffers and thread stacks
    # are NOT limited by -Xmx, hence deliberately conservative guard.
    cap,used=detect_cgroup_memory()
    estimate=args.jvms*(args.heap_max_mb+48)*1024*1024
    available=max(0,cap-used) if cap is not None and used is not None else None
    if available is not None and estimate>available*0.85 and not args.unsafe_resource_override:
        raise ValueError(f'Estimated JVM budget {estimate//1048576} MiB exceeds '
          f'85% of cgroup headroom {available//1048576} MiB; reduce --jvms/--heap-max-mb '
          'or explicitly use --unsafe-resource-override')
    return dict(memory_limit_bytes=cap,memory_used_prelaunch_bytes=used,
                estimated_producer_budget_bytes=estimate,overridden=args.unsafe_resource_override)

def load_records(folder,jvms,requests,expected_sequences=None):
    records=[];seqs=set();errors=[]
    for i in range(jvms):
        path=folder/f'agent-{i:03d}.tsv'
        if not path.exists():errors.append(f'agent {i} missing output');continue
        with path.open(newline='') as f:
            reader=csv.DictReader(f,delimiter='\t')
            for r in reader:
                try:
                    s=int(r['sequence']); slot=int(r['slot']); var=int(r['variant']);
                    status=int(r['status']);lat=int(r['latency_ns']);
                    if s in seqs:errors.append(f'duplicate sequence {s}')
                    seqs.add(s)
                    if not (0<=s<requests and lat>=0 and var in (0,1) and slot>=0):errors.append(f'invalid record sequence {s}')
                    records.append({'sequence':s,'slot':slot,'variant':var,'status':status,'latency_ns':lat,
                      'id':r['id'],'error':r['error'],'send_ms':int(r['send_epoch_ms']),'end_ms':int(r['end_epoch_ms']),'agent':i,'schedule_lag_ms':float(r.get('schedule_lag_ms','0') or 0)})
                except (ValueError,KeyError) as e:errors.append(f'bad row in {path.name}: {e}')
    expected=set(range(requests)) if expected_sequences is None else set(expected_sequences)
    if seqs!=expected:errors.append(f'missing={len(expected-seqs)} unexpected={len(seqs-expected)} sequences')
    return records,errors

def observed_parallelism(records):
    """Concurrent in-flight HTTP samples and distinct active JVMs, using client timestamps.
    All JVMs must share the same machine clock for meaningful aggregation.
    """
    events=[]
    for e in records:
        events.append((e['send_ms'],1,e['agent']))
        events.append((e['end_ms'],-1,e['agent']))
    # End before start at an equal millisecond; coarse millisecond precision is conservative.
    events.sort(key=lambda t:(t[0],t[1]))
    active=collections.Counter();total=peak=distinct_peak=0
    for _,direction,agent in events:
        if direction>0:
            active[agent]+=1;total+=1
        elif active[agent]>0:
            active[agent]-=1;total-=1
            if not active[agent]:del active[agent]
        peak=max(peak,total);distinct_peak=max(distinct_peak,len(active))
    return peak,distinct_peak

def audit(records,keys):
    violations=[];winner={}; ids={};created=collections.Counter(); statuses=collections.Counter()
    for e in records:
        statuses[str(e['status']) if e['status']>=0 else 'transport_error']+=1
        if e['slot']>=keys:violations.append(f'out of range key slot {e["slot"]}')
        if e['status'] in (200,202):
            if not e['id']:violations.append(f'{e["sequence"]}: accepted without UUID')
            prev=winner.setdefault(e['slot'],e['variant'])
            if prev!=e['variant']:violations.append(f'{e["sequence"]}: TWO ACCEPTED PAYLOADS for idempotency key {e["slot"]}')
            old=ids.setdefault(e['slot'],e['id'])
            if old!=e['id']:violations.append(f'{e["sequence"]}: TWO UUIDs for idempotency key {e["slot"]}')
            if e['status']==202:created[e['slot']]+=1
        if e['status']==409 and e['id']:violations.append(f'{e["sequence"]}: unexpected ID on conflict')
    for slot,num in created.items():
        if num!=1:violations.append(f'key {slot} had {num} creations')
    for e in records:
        if e['status']==200 and created[e['slot']]!=1:
            violations.append(f'{e["sequence"]}: replay without exactly one creation in this run')
        if e['status']==409 and created[e['slot']]!=1:
            violations.append(f'{e["sequence"]}: conflict without exactly one creation in this run')
        if e['status']==409 and e['slot'] in winner and e['variant']==winner[e['slot']]:
            violations.append(f'{e["sequence"]}: conflict reported for winning payload of key {e["slot"]}')
    return violations, winner, ids,dict(statuses)

def get_status(url,token,uid,timeout_ms):
    req=urllib.request.Request(url+'/api/v1/requests/'+uid,headers={'Authorization':'Bearer '+token})
    try:
        with urllib.request.urlopen(req,timeout=timeout_ms/1000) as r:return r.status,json.loads(r.read())
    except urllib.error.HTTPError as e:return e.code,{}

def verify(url,read_token,run_id,winner,ids,timeout_ms,await_seconds):
    if not read_token:raise ValueError('DEROYQUE_SIM_READ_TOKEN required with --verify-state')
    failures=[]; final_states={};until=time.monotonic()+await_seconds
    # Repeated polls apply only after the load measurement, and are NOT part of POST throughput.
    remaining=set(ids)
    while remaining:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(32,len(remaining))) as pool:
            future={pool.submit(get_status,url,read_token,ids[slot],timeout_ms):slot for slot in remaining}
            next_remaining=set()
            for f,slot in future.items():
                try:code,body=f.result()
                except Exception as e:failures.append(f'GET {slot}: {type(e).__name__}');continue
                expected_customer=f'c-{slot}'+('-conflict' if winner[slot] else '')
                expected_product='different' if winner[slot] else 'standard'
                if code!=200 or body.get('id')!=ids[slot] or body.get('externalId')!=f'{run_id}-{slot}' or body.get('customerId')!=expected_customer or body.get('productCode')!=expected_product:
                    failures.append(f'GET {slot} inconsistent: HTTP {code}, id={body.get("id")}, state={body.get("status")}')
                else:
                    status=body.get('status','unknown');final_states[slot]=status
                    if status not in ('DONE','FAILED') and await_seconds>0:next_remaining.add(slot)
            if not next_remaining or time.monotonic()>=until:
                if next_remaining:failures.append(f'{len(next_remaining)} accepted request(s) not terminal before deadline')
                break
            remaining=next_remaining
            time.sleep(min(.4,max(0,until-time.monotonic())))
    # Count each unique request exactly once, using its last observed state.
    return failures,dict(collections.Counter(final_states.values()))

def run(argv=None):
    args=parse_args(argv)
    if args.auto_jvms:args.jvms=choose_jvms(args)
    validate_target(args)
    if not os.environ.get('DEROYQUE_SIM_WRITE_TOKEN'):raise ValueError('Set DEROYQUE_SIM_WRITE_TOKEN (do not include tokens in command arguments)')
    if args.verify_state and not os.environ.get('DEROYQUE_SIM_READ_TOKEN'):raise ValueError('Set DEROYQUE_SIM_READ_TOKEN for verification')
    resources=resource_report(args)
    run_id=args.run_id or 'load-'+uuid.uuid4().hex[:12]
    out=args.output_dir or ROOT/'results'/run_id
    out.mkdir(parents=True,exist_ok=False)
    jar=ROOT/'build'/'deroyque-input-simulator.jar'
    if not jar.exists():raise ValueError('Build first: ./simulator/build.sh')
    commands=[];processes=[]
    # Precompute deterministic global schedule, partitioned by host and JVM. The same
    # idempotency slot is shared across all independent producer processes.
    schedule_files=[]
    for agent in range(args.jvms):
        f=(out/f'plan-{agent:03d}.tsv').open('w',encoding='ascii')
        schedule_files.append(f)
        f.write('sequence\tslot\tvariant\toffset_ms\n')
    last_schedule_offset=0
    local_total=0
    try:
        for seq,slot,variant,offset in create_plan(args):
            if seq%args.shard_count!=args.shard_index:continue
            last_schedule_offset=offset
            local_agent=local_total%args.jvms
            schedule_files[local_agent].write(f'{seq}\t{slot}\t{variant}\t{offset}\n')
            local_total+=1
    finally:
        for f in schedule_files:f.close()
    start_at=args.start_at_ms if args.start_at_ms is not None else int(time.time()*1000)+args.start_delay_ms
    if start_at<int(time.time()*1000)+500:raise ValueError('Start epoch is too soon; provide a future barrier with time for JVM startup')
    for agent in range(args.jvms):
        cmd=[args.java,f'-Xms{args.heap_min_mb}m',f'-Xmx{args.heap_max_mb}m',f'-XX:ActiveProcessorCount={args.active_processors}','-cp',str(jar),'demo.simulator.LoadAgent',
          f'--agent={agent}',f'--agents={args.jvms}',f'--requests={args.requests}',f'--keys={args.keys}',
          f'--concurrency={args.concurrency}',f'--conflict-percent={args.conflict_percent}',
          f'--timeout-ms={args.timeout_ms}',f'--run={run_id}',f'--url={args.url.rstrip("/")}',
          f'--output={out/f"agent-{agent:03d}.tsv"}',f'--start-at-ms={start_at}',
          f'--rate-per-agent={args.rate/args.jvms}',f'--plan={out/f"plan-{agent:03d}.tsv"}']
        if args.producer_cpus:cmd=['taskset','-c',format_cpus(parse_cpus(args.producer_cpus)),*cmd]
        commands.append(cmd)
    print(f'START shard={args.shard_index}/{args.shard_count} run={run_id} target={args.url} producers={args.jvms} max_inflight={args.jvms*args.concurrency} '
          f'producer_cpus={args.producer_cpus or "all-available"} POSTs={args.requests} keys={args.keys} profile={args.profile} global_rate={args.rate or "unlimited"} output={out}',flush=True)
    try:
        for i,cmd in enumerate(commands):
            logfile=(out/f'agent-{i:03d}.log').open('w')
            p=subprocess.Popen(cmd,stdout=logfile,stderr=subprocess.STDOUT,env=os.environ.copy())
            processes.append((p,logfile))
        # Timeouts scaled to scheduled work, never hang forever on lost agents.
        max_per_agent=math.ceil(local_total/args.jvms)
        test_s=max(90,args.timeout_ms/1000*4+35 + last_schedule_offset/1000)
        deadline=time.monotonic()+test_s
        for p,_ in processes:
            p.wait(timeout=max(1,deadline-time.monotonic()))
    except (subprocess.TimeoutExpired,KeyboardInterrupt):
        for p,_ in processes:
            if p.poll() is None:p.terminate()
        for p,_ in processes:
            try:p.wait(timeout=3)
            except subprocess.TimeoutExpired:p.kill();p.wait()
        raise
    finally:
        for _,f in processes:f.close()
    records,errors=load_records(out,args.jvms,args.requests,range(args.shard_index,args.requests,args.shard_count))
    for i,(p,_) in enumerate(processes):
        if p.returncode!=0:errors.append(f'agent {i} process exited {p.returncode} (inspect agent log)')
    if args.shard_count==1:
        violations,winner,ids,statuses=audit(records,args.keys)
    else:
        # Only cross-host reconciliation can establish one 202 creator per key.
        # Shard-local 200/409 without 202 is normal, not a failure.
        violations=[];winner={};ids={}
        statuses=dict(collections.Counter(str(r['status']) if r['status']>=0 else 'transport_error' for r in records))
        for r in records:
            if r['status'] in (200,202) and not r['id']:violations.append(f'{r["sequence"]}: missing admitted UUID')
            if r['status']==409 and r['id']:violations.append(f'{r["sequence"]}: conflict has UUID')
    lat=[e['latency_ns'] for e in records if e['status']>=0]
    start=min((e['send_ms'] for e in records),default=0)
    end=max((e['end_ms'] for e in records),default=0)
    elapsed=(end-start)/1000 if end>start else 0
    peak_inflight,peak_agents=observed_parallelism(records)
    errors_http=sum(e['status'] not in (200,202,409) for e in records)
    error_pct=100*errors_http/max(1,len(records))
    state_failures=[];state_counts={}
    if args.verify_state:
        state_failures,state_counts=verify(args.url.rstrip('/'),os.environ.get('DEROYQUE_SIM_READ_TOKEN'),run_id,winner,ids,args.timeout_ms,args.await_done_seconds)
    lag=[e['schedule_lag_ms'] for e in records]
    report={'run_id':run_id,'target':args.url,'resource_budget':resources,'plan':{'profile':args.profile,
       'distribution':args.distribution,'duration_seconds':args.duration_seconds,'seed':args.seed,
       'ramp_start_rate':args.ramp_start_rate,'burst_rate':args.burst_rate,
       'burst_width_seconds':args.burst_width_seconds,'burst_period_seconds':args.burst_period_seconds,
       'hotspot_percent':args.hotspot_percent,'hotspot_keys_percent':args.hotspot_keys_percent,'skew_power':args.skew_power,
       'heap_max_mb':args.heap_max_mb,'active_processors':args.active_processors,
       'auto_jvms':args.auto_jvms,'auto_max_jvms':args.auto_max_jvms,
       'shard_index':args.shard_index,'shard_count':args.shard_count,'start_at_ms':start_at,
       'global_attempted_posts':args.requests,'local_attempted_posts':local_total,
       'jvms':args.jvms,'concurrency_per_jvm':args.concurrency,
       'attempted_posts':args.requests,'shared_keys':args.keys,'conflict_percent':args.conflict_percent,'target_qps':args.rate},
      'observed':{'records':len(records),'admitted_keys_observed':len(ids) if args.shard_count==1 else None,'http_statuses':statuses,
        'unexpected_http_or_transport':errors_http,'error_pct':round(error_pct,4),
        'peak_inflight_http':peak_inflight,'peak_jvms_with_inflight_http':peak_agents,
        'wall_post_seconds':round(elapsed,4),'post_attempts_per_second':round(len(records)/elapsed,3) if elapsed else 0,
        'accepted_200_202_per_second':round(sum(e['status'] in (200,202) for e in records)/elapsed,3) if elapsed else 0,
        'latency_ms':{str(q):duration_ms_to_percentile(lat,q) for q in (50,90,95,99,100)},
        'schedule_lag_ms':{str(q):round(sorted(lag)[max(0,math.ceil(len(lag)*q/100)-1)],3) if lag else None for q in (50,95,99)},
        'verified_states':state_counts},
      'violations':violations,'harness_errors':errors,'state_verification_errors':state_failures,
      'warning':('Shard-only checks; globally authoritative idempotency audit deferred to distributed_merge.py. ' if args.shard_count>1 else '')+'Accepted HTTP != completed external effect; no exactly-once guarantee. Cross-host overlap requires synchronized host clocks.'}
    failed=bool(violations or errors or state_failures or error_pct>args.max_error_percent)
    report['outcome']='FAIL' if failed else ('SHARD_PASS_GLOBAL_AUDIT_PENDING' if args.shard_count>1 else 'PASS')
    (out/'summary.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
    print(json.dumps(report,indent=2,sort_keys=True),flush=True)
    print(f'FINAL {report["outcome"]}: invariant_violations={len(violations)}, harness_errors={len(errors)}, '
      f'verification_errors={len(state_failures)}, unexpected_http_transport={errors_http}/{len(records)}',flush=True)
    return 1 if failed else 0

if __name__=='__main__':
    try:sys.exit(run())
    except Exception as e:
        print(f'SIMULATOR_FATAL {type(e).__name__}: {e}',file=sys.stderr)
        sys.exit(2)
