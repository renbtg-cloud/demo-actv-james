#!/usr/bin/env python3
"""Conservative multi-tier saturation explorer; never targets remote hosts without explicit opt-in.

Runs the existing first-class multi-JVM ingress generator without changing its semantics.
Optional diagnostics attach to an explicitly selected service JVM; process telemetry
separates service from producer JVMs and shared-host cgroup resource constraints.
"""
import argparse
import csv
import json
import math
import os
import signal
import pathlib
import subprocess
import sys
import time
import uuid
from thread_diagnostics import (cgroup_info,descendant_java_pids,proc_info,snapshot,
                                jfr_start,jfr_stop,jfr_events)
from run import parse_args,validate_target,resource_report
from cpu_placement import parse_cpus,format_cpus,pid_cpus,proc_cpu_threads

ROOT=pathlib.Path(__file__).resolve().parent

def tier(value):
    try:
        jvms,rate,requests=(int(x) if i!=1 else float(x) for i,x in enumerate(value.split(':')))
    except (ValueError,TypeError):raise argparse.ArgumentTypeError('Tier requires JVMS:QPS:REQUESTS')
    if not (1<=jvms<=256 and 0<rate<=100000 and 1<=requests<=10000000 and jvms<=requests and all(math.isfinite(x) for x in (rate,))):
        raise argparse.ArgumentTypeError('Invalid tier bounds')
    return (jvms,rate,requests)

def parse(argv=None):
    p=argparse.ArgumentParser(description='Deroyque incremental multi-JVM saturation explorer (authorized targets only)')
    p.add_argument('--tier',type=tier,action='append',required=True,metavar='JVMS:QPS:POSTS')
    p.add_argument('--url',default='http://127.0.0.1:18081')
    p.add_argument('--allow-remote',action='store_true')
    p.add_argument('--service-pid',type=int,help='explicit service JVM PID, checked for Java command line')
    p.add_argument('--output-dir',type=pathlib.Path,required=True)
    p.add_argument('--concurrency',type=int,default=8)
    p.add_argument('--producer-cpus',help='CPU placement for all producers; must be subset of CPUs available to this process')
    p.add_argument('--expected-service-cpus',help='Fail if service leader CPU mask differs from expectation; launch service under taskset')
    p.add_argument('--heap-max-mb',type=int,default=64)
    p.add_argument('--heap-min-mb',type=int,default=16)
    p.add_argument('--active-processors',type=int,default=2)
    p.add_argument('--keys-ratio',type=float,default=.25)
    p.add_argument('--distribution',choices=('sequential','hotspot','skewed','unique'),default='hotspot')
    p.add_argument('--conflict-percent',type=int,default=30)
    p.add_argument('--sample-period',type=float,default=.25)
    p.add_argument('--threads',choices=('off','service','service-and-producer'),default='service')
    p.add_argument('--jfr',action='store_true',help='Bounded JFR recording of the service JVM per tier')
    p.add_argument('--max-schedule-lag-ms',type=float,default=250)
    p.add_argument('--max-p95-ms',type=float,default=10000)
    p.add_argument('--min-rate-ratio',type=float,default=.8)
    p.add_argument('--max-error-percent',type=float,default=0)
    p.add_argument('--start-delay-ms',type=int,default=3000)
    p.add_argument('--verify-state',action='store_true')
    p.add_argument('--max-total-posts',type=int,default=20000)
    p.add_argument('--continue-on-degradation',action='store_true')
    a=p.parse_args(argv)
    try:
        if a.producer_cpus is not None:parse_cpus(a.producer_cpus)
        if a.expected_service_cpus is not None:
            expected=parse_cpus(a.expected_service_cpus)
            actual=set(pid_cpus(a.service_pid) or ())
            if not a.service_pid or expected!=actual:raise ValueError(f'Service affinity mismatch: expected {sorted(expected)}, actual {sorted(actual)}')
            thread_masks=proc_cpu_threads(a.service_pid)
            if thread_masks is None or thread_masks['threads_with_other_affinity']:
                raise ValueError('Service OS threads do not all share the expected CPU placement')
            if a.producer_cpus is not None and (parse_cpus(a.producer_cpus) & expected):raise ValueError('Producer and service CPU sets overlap; refuse misleading partition benchmark')
    except ValueError as err:p.error(str(err))
    if a.output_dir.exists():p.error('--output-dir already exists; never overwrite benchmark evidence')
    if sum(t[2] for t in a.tier)>a.max_total_posts:p.error('Planned POST count exceeds safety --max-total-posts')
    for name in ('sample_period','min_rate_ratio','keys_ratio'):
        x=getattr(a,name)
        if not math.isfinite(x) or x<=0:p.error(f'--{name.replace("_","-")} must be positive finite')
    if a.keys_ratio>1:p.error('--keys-ratio must be <=1')
    if not 0<a.min_rate_ratio<=1:p.error('--min-rate-ratio must be <=1')
    if a.concurrency<1 or a.concurrency>256:p.error('--concurrency must be 1..256')
    if a.service_pid is not None:
        pth=pathlib.Path(f'/proc/{a.service_pid}/cmdline')
        try:cmd=pth.read_bytes()
        except OSError:p.error('--service-pid is not running')
        if b'java' not in cmd.lower() or b'DeroyqueApplication' not in cmd:p.error('--service-pid must refer to DeroyqueApplication Java process')
    if a.threads!='off' and not a.service_pid:p.error('--service-pid required for --threads')
    if a.jfr and not a.service_pid:p.error('--service-pid required for --jfr')
    if a.sample_period<0.1 or a.sample_period>10:p.error('--sample-period must be 0.1..10s')
    if a.heap_max_mb < 32 or a.heap_min_mb < 1 or a.heap_min_mb > a.heap_max_mb:
        p.error('Invalid JVM heap settings')
    if a.max_error_percent<0 or a.max_error_percent>100:p.error('--max-error-percent must be 0..100')
    if a.max_p95_ms<=0 or a.max_schedule_lag_ms<0:p.error('Invalid latency/lag thresholds')
    for jvms,rate,posts in a.tier:
        args=parse_args(['--url',a.url,'--jvms',str(jvms),'--requests',str(posts),'--keys',str(max(1,int(posts*a.keys_ratio))),
                '--rate',str(rate),'--concurrency',str(a.concurrency),'--heap-max-mb',str(a.heap_max_mb),
                '--heap-min-mb',str(a.heap_min_mb),'--active-processors',str(a.active_processors),
                '--distribution',a.distribution if a.distribution!='unique' else 'sequential',
                '--conflict-percent',str(a.conflict_percent),*( ['--producer-cpus',a.producer_cpus] if a.producer_cpus else []),*( ['--allow-remote'] if a.allow_remote else [])])
        validate_target(args);resource_report(args)
    return a

def describe(result,rate,p95limit,laglimit,ratio):
    obs=result.get('observed',{})
    if result.get('outcome')!='PASS':return 'FAIL_INVARIANT_OR_TRANSPORT'
    # Throughput alone is not proof of a server bottleneck, especially under simultaneous
    # client and server CPU contention. Require schedule-lag and process telemetry.
    p95=obs.get('latency_ms',{}).get('95')
    lag=obs.get('schedule_lag_ms',{}).get('95')
    actual=obs.get('post_attempts_per_second',0)
    if lag is None or p95 is None:return 'INCONCLUSIVE_MISSING_MEASUREMENTS'
    if lag>laglimit:return 'GENERATOR_UNABLE_TO_MEET_SCHEDULE_OR_SHARED_CONTENTION'
    if p95>p95limit:return 'LATENCY_LIMIT_EXCEEDED_ROOT_CAUSE_UNDETERMINED'
    if actual<rate*ratio:return 'BELOW_TARGET_ROOT_CAUSE_UNDETERMINED'
    return 'TARGET_SUSTAINED_WITHIN_THRESHOLDS'

def integrate_producer_cpu(producers,previous,observed):
    # Preserve CPU already observed when short-lived JVMs disappear between samples.
    # This is a LOWER BOUND: CPU used before first visibility/after last sample can be missed.
    for process in producers:
        pid=process['pid']; now=process['cpu_s']
        observed+=max(0,now-previous.get(pid,0))
        previous[pid]=now
    return observed

def cpu_load(samples,key):
    valid=[s for s in samples if s.get(key) is not None]
    if len(valid)<2:return None
    seconds=(valid[-1]['epoch_ms']-valid[0]['epoch_ms'])/1000
    delta=valid[-1][key]-valid[0][key]
    return round(delta/seconds,3) if seconds>0 and delta>=0 else None

def stats(samples,prefix):
    valid=[s[prefix] for s in samples if s.get(prefix) is not None]
    return {'min':min(valid),'max':max(valid),'avg':round(sum(valid)/len(valid),2)} if valid else None

def execute(a):
    out=a.output_dir.resolve();out.mkdir(parents=True)
    rows=[]; stop_reason=None
    for stage,(jvms,rate,posts) in enumerate(a.tier,1):
        if stop_reason:break
        folder=out/f'tier-{stage:02d}-{jvms}jvm';folder.mkdir()
        keys=posts if a.distribution=='unique' else max(1,int(posts*a.keys_ratio))
        cmd=[sys.executable,'-u',str(ROOT/'run.py'),'--url',a.url,'--jvms',str(jvms),
           '--requests',str(posts),'--keys',str(keys),'--concurrency',str(a.concurrency),
           '--rate',str(rate),'--profile','steady','--distribution',a.distribution,
           '--conflict-percent',str(a.conflict_percent),'--start-delay-ms',str(a.start_delay_ms),
           '--heap-min-mb',str(a.heap_min_mb),'--heap-max-mb',str(a.heap_max_mb),
           '--active-processors',str(a.active_processors),'--max-error-percent',str(a.max_error_percent),
           '--run-id','sat-'+uuid.uuid4().hex[:10], '--output-dir',str(folder/'workload')]
        if a.producer_cpus:cmd.extend(['--producer-cpus',a.producer_cpus])
        if a.allow_remote:cmd.append('--allow-remote')
        if a.verify_state:cmd.append('--verify-state')
        cmdlog=(folder/'run.log').open('w');p=None; samples=[];diagnostics=[]
        recording='deroyque_sat_'+uuid.uuid4().hex[:9];jfr_result=None;producer_snapshot=False
        previous_producer_cpu={};observed_producer_cpu=0.0
        print(f'TIER {stage}/{len(a.tier)}: {jvms} JVMs, target {rate:g} POST/s, {posts} POSTs',flush=True)
        try:
            if a.jfr:
                from thread_diagnostics import jfr_configure_focus
                settings=jfr_configure_focus(folder/'focus.jfc')
                jfr_result={'settings':settings,'start':jfr_start(a.service_pid,recording,settings_path=folder/'focus.jfc' if settings.get('ok') else None)}
            p=subprocess.Popen(cmd,cwd=ROOT.parent,stdout=cmdlog,stderr=subprocess.STDOUT,start_new_session=True)
            # Delay until traffic is being scheduled, but collect before fast tiers end.
            next_dump=time.monotonic()+max(0.6,a.start_delay_ms/1000-0.25)
            while p.poll() is None:
                cg=cgroup_info();service=proc_info(a.service_pid) if a.service_pid else None
                producers=descendant_java_pids(p.pid)
                observed_producer_cpu=integrate_producer_cpu(producers,previous_producer_cpu,observed_producer_cpu)
                sample={'epoch_ms':int(time.time()*1000),'cgroup_memory_bytes':cg.get('memory_bytes'),
                        'cgroup_cpu_usage_usec':cg.get('cpu_stat',{}).get('usage_usec'),
                        'cgroup_nr_throttled':cg.get('cpu_stat',{}).get('nr_throttled'),
                        'cgroup_oom_kill':cg.get('memory_events',{}).get('oom_kill'),
                        'service_rss_bytes':service['rss_bytes'] if service else None,
                        'service_cpu_seconds':service['cpu_s'] if service else None,
                        'service_os_threads':service['os_threads'] if service else None,
                        'producer_jvms':len(producers),'producer_cpu_masks':sorted({tuple(pid_cpus(x['pid']) or ()) for x in producers}), 'producer_rss_bytes':sum(x['rss_bytes'] for x in producers),
                        'producer_os_threads':sum(x['os_threads'] for x in producers),
                        'producer_cpu_seconds':round(observed_producer_cpu,3)}
                samples.append(sample)
                if a.threads!='off' and producers and time.monotonic()>=next_dump:
                    diagnostics.append({'role':'service',**snapshot(a.service_pid,folder/'diagnostics','service-mid')})
                    if a.threads=='service-and-producer' and producers:
                        diagnostics.append({'role':'producer',**snapshot(producers[0]['pid'],folder/'diagnostics','producer-mid')})
                        producer_snapshot=True
                    next_dump=float('inf') # bounded once per stage, avoid diagnostic-induced saturation
                time.sleep(a.sample_period)
            rc=p.wait()
            if a.threads!='off' and not diagnostics:
                diagnostics.append({'role':'service',**snapshot(a.service_pid,folder/'diagnostics','service-end')})
        finally:
            if p and p.poll() is None:
                # run.py's child JVMs inherit the coordinator's process group.
                # Kill ONLY the group created for this owned load-generator tier.
                try:os.killpg(p.pid,signal.SIGTERM)
                except ProcessLookupError:pass
                try:p.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    try:os.killpg(p.pid,signal.SIGKILL)
                    except ProcessLookupError:pass
                    p.wait()
            if jfr_result is not None:
                recording_file=folder/'service.jfr'
                jfr_result['stop']=jfr_stop(a.service_pid,recording,recording_file)
                if recording_file.is_file():jfr_result['events']=jfr_events(recording_file)
                (folder/'jfr-summary.json').write_text(json.dumps(jfr_result,indent=2)+'\n')
            cmdlog.close()
        samples_path=folder/'telemetry.csv'
        with samples_path.open('w',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=list(samples[0]) if samples else ['epoch_ms'])
            writer.writeheader();writer.writerows(samples)
        (folder/'thread-summary.json').write_text(json.dumps(diagnostics,indent=2)+'\n')
        summary_path=folder/'workload'/'summary.json'
        result=json.loads(summary_path.read_text()) if summary_path.is_file() else {'outcome':'FAIL','observed':{},'harness_errors':['missing workload summary']}
        verdict=describe(result,rate,a.max_p95_ms,a.max_schedule_lag_ms,a.min_rate_ratio)
        if rc!=0 and 'Estimated JVM budget' in (folder/'run.log').read_text():
            verdict='RESOURCE_GUARD_REJECTED_STAGE_NOT_RUN'
        row={'tier':stage,'planned_jvms':jvms,'planned_post_rate':rate,'planned_posts':posts,
             'result':result.get('outcome','UNKNOWN'),'process_exit':rc,'verdict':verdict,
             'observed_posts':result.get('observed',{}).get('records',0),
             'unexpected_http':result.get('observed',{}).get('unexpected_http_or_transport'),
             'observed_qps':result.get('observed',{}).get('post_attempts_per_second'),
             'p95_ms':result.get('observed',{}).get('latency_ms',{}).get('95'),
             'schedule_p95_ms':result.get('observed',{}).get('schedule_lag_ms',{}).get('95'),
             'peak_active_jvms_http':result.get('observed',{}).get('peak_jvms_with_inflight_http'),
             'peak_producer_processes_sampled':max((s['producer_jvms'] for s in samples),default=0),
             'service_cpus_observed':pid_cpus(a.service_pid) if a.service_pid else None,
             'service_thread_cpu_masks':proc_cpu_threads(a.service_pid) if a.service_pid else None,
             'producer_cpus_configured':sorted(parse_cpus(a.producer_cpus)) if a.producer_cpus else None,
             'producer_cpu_masks_observed':sorted({str(mask) for sm in samples for mask in sm['producer_cpu_masks']}),
             'peak_producer_rss_bytes':max((s['producer_rss_bytes'] for s in samples),default=0),
             'peak_service_os_threads':max((s['service_os_threads'] or 0 for s in samples),default=0),
             'service_thread_dump_errors':sum(len(d.get('errors',[])) for d in diagnostics if d.get('role')=='service'),
             'thread_dump_errors_total':sum(len(d.get('errors',[])) for d in diagnostics),
             'thread_snapshots':len(diagnostics),
             'service_cpu_cores_avg':cpu_load(samples,'service_cpu_seconds'),
             'producer_cpu_cores_avg':cpu_load(samples,'producer_cpu_seconds'),
             'cgroup_cpu_cores_avg':round(cpu_load(samples,'cgroup_cpu_usage_usec')/1_000_000,3) if cpu_load(samples,'cgroup_cpu_usage_usec') is not None else None,
             'service_peak_rss_bytes':max((s['service_rss_bytes'] or 0 for s in samples),default=0),
             'cgroup_peak_memory_bytes':max((s['cgroup_memory_bytes'] or 0 for s in samples),default=0),
             'jfr_recorded':bool(jfr_result and jfr_result.get('start',{}).get('ok') and jfr_result.get('stop',{}).get('ok') and jfr_result.get('events',{}).get('counts') is not None),
             'virtual_thread_pinned_events':(jfr_result.get('events',{}).get('counts',{}).get('jdk.VirtualThreadPinned') if jfr_result else None),
             'virtual_thread_pinned_ms_total':(jfr_result.get('events',{}).get('virtual_thread_pinned_duration_total_ms') if jfr_result else None),
             'virtual_thread_pinned_duration_buckets':(jfr_result.get('events',{}).get('virtual_thread_pinned_duration_buckets') if jfr_result else None),
             'cgroup_oom_kills_delta':((samples[-1]['cgroup_oom_kill'] or 0)-(samples[0]['cgroup_oom_kill'] or 0)) if len(samples)>1 else None,
             'cgroup_nr_throttled_delta':((samples[-1]['cgroup_nr_throttled'] or 0)-(samples[0]['cgroup_nr_throttled'] or 0)) if len(samples)>1 else None}
        rows.append(row)
        (folder/'tier-summary.json').write_text(json.dumps(row,indent=2)+'\n')
        print(f"TIER RESULT {stage}: {verdict} {row['observed_posts']}/{posts} POSTs observed, "
              f"{row['observed_qps']} req/s, {row['peak_active_jvms_http']} JVMs overlapping HTTP",flush=True)
        if not a.continue_on_degradation and (rc!=0 or verdict!='TARGET_SUSTAINED_WITHIN_THRESHOLDS' or (row['cgroup_oom_kills_delta'] or 0)>0):
            stop_reason=f'stopped at tier {stage}: {verdict}, exit={rc}; OOM delta={row["cgroup_oom_kills_delta"]}'
    report={'tiers':rows,'stop_reason':stop_reason,'methodology':{
      'throughput':'HTTP POST admission attempts, not provider completions',
      'cpu_placement':'Optional taskset affinity is placement only; shared cgroup quotas and broker/DB still contend. Service launched separately under taskset. No remote host used.',
      'sampling':'/proc service and direct producer children plus shared cgroup v2 counters; producer CPU integration is a lower bound across sampled short-lived JVMs',
      'thread_dumps':'One bounded JDK thread dump during tier if Java agents are detected; service-only fallback after tier, JSON containers + platform state text',
      'jfr':'Optional profile recording on selected service JVM; zero recorded events does not prove absence',
      'verdict':'Heuristic thresholds; no root cause proven; shared CPU means client may throttle server',
      'no_causal_inference':'Single-host test cannot attribute cgroup pressure to only one component; producer CPU sampling omits unobserved intervals'}}
    (out/'saturation.json').write_text(json.dumps(report,indent=2)+'\n')
    with (out/'tiers.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]) if rows else ['tier']);writer.writeheader();writer.writerows(rows)
    lines=['# Deroyque ingress saturation exploration','',
        'These measurements are **POST admissions**, not confirmed provider effects. Multiple JVMs share host CPU/memory; bottlenecks are not automatically attributed to the server.','',
        '| Tier | Producers | Target /s | Observed /s | p95 ms | Lag p95 ms | JVMs actually overlapping HTTP | Outcome |',
        '|---|---:|---:|---:|---:|---:|---:|---|']
    for r in rows:lines.append(f"| {r['tier']} | {r['planned_jvms']} | {r['planned_post_rate']} | {r['observed_qps']} | {r['p95_ms']} | {r['schedule_p95_ms']} | {r['peak_active_jvms_http']} | {r['verdict']} |")
    lines+=['',f'Stopping condition: {stop_reason or "all planned tiers completed"}.','',
            'Thread dump snapshots are under each tier/diagnostics; sampled OS thread counts and cgroup counters are in telemetry.csv. JFR results are included only if --jfr was enabled. Stages rejected by memory safety did not execute traffic.','',
            'Affinity settings are placement only, not reserved CPUs, cpusets or independent hosts. Producer CPU masks and service per-thread affinity evidence are retained in each tier summary.',
            '', 'Thread state counters apply to platform threads only; JDK 21 virtual-thread JSON stacks provide no general RUNNABLE/BLOCKED breakdown. For virtual-thread pinning, JFR events must be configured and interpreted, not inferred from dump counts.','']
    (out/'SATURATION-REPORT.md').write_text('\n'.join(lines))
    return 1 if any(r['result']!='PASS' or r['process_exit']!=0 or r['verdict']!='TARGET_SUSTAINED_WITHIN_THRESHOLDS' or (r['cgroup_oom_kills_delta'] or 0)>0 for r in rows) else 0

if __name__=='__main__':
    try:sys.exit(execute(parse()))
    except (ValueError, OSError) as exc:
        print(f'SATURATION_FATAL: {type(exc).__name__}: {exc}',file=sys.stderr);sys.exit(2)
