#!/usr/bin/env python3
"""Repeatable same-host A/B benchmark of Deroyque opt-in ingress metrics.

Two OFF and two ON trials (ABBA), fresh schema/queue/provider each trial.
Never stops PostgreSQL, RabbitMQ or other existing services. This benchmark
measures the combined local laboratory and is NOT a pure overhead estimate.
"""
import argparse
import json
import os
from pathlib import Path
import re
import signal
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

ROOT=Path(__file__).resolve().parents[1]
SIM=ROOT/'simulator'
sys.path.insert(0,str(SIM))
from server_metrics import fetch

ORDER=('OFF','ON','ON','OFF')

def parse_args(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output-dir',type=Path,required=True)
    p.add_argument('--private-dir',type=Path,default=ROOT/'.local'/'ab-private')
    p.add_argument('--jvms',type=int,default=6)
    p.add_argument('--concurrency',type=int,default=8)
    p.add_argument('--requests',type=int,default=240)
    p.add_argument('--keys',type=int,default=80)
    p.add_argument('--rate',type=float,default=65)
    p.add_argument('--heap-max-mb',type=int,default=48)
    p.add_argument('--start-delay-ms',type=int,default=5500)
    p.add_argument('--api-port',type=int,default=18181)
    p.add_argument('--provider-port',type=int,default=18182)
    p.add_argument('--sample-period',type=float,default=0.25)
    a=p.parse_args(argv)
    if a.jvms<1 or a.requests<a.jvms or a.keys<1 or a.rate<=0 or a.concurrency<1 or a.sample_period<0.1 or a.heap_max_mb<32:
        p.error('Invalid workload/resource settings')
    if a.start_delay_ms<2000 or not (1025<=a.api_port<=65535 and 1025<=a.provider_port<=65535) or a.api_port==a.provider_port:
        p.error('Invalid barrier or port')
    return a

def basic_get(url,token=None,timeout=2):
    h={'Authorization':'Bearer '+token} if token else {}
    req=urllib.request.Request(url,headers=h)
    try:
        with urllib.request.urlopen(req,timeout=timeout) as response:return response.status
    except urllib.error.HTTPError as exc:return exc.code
    except (TimeoutError,ConnectionError,OSError,urllib.error.URLError):return None

def proc_info(pid):
    try:
        stat=(Path('/proc')/str(pid)/'stat').read_text()
        fields=stat[stat.rfind(')')+2:].split()
        # fields[0] is state, fields[1] is ppid, fields[11]/[12] are utime/stime
        cpu=(int(fields[11])+int(fields[12]))/os.sysconf('SC_CLK_TCK')
        status=(Path('/proc')/str(pid)/'status').read_text()
        rss_match=re.search(r'^VmRSS:\s*(\d+)\s*kB',status,re.M)
        return {'ppid':int(fields[1]),'cpu_seconds':cpu,'rss_bytes':int(rss_match.group(1))*1024 if rss_match else None}
    except (OSError,ValueError,IndexError):return None

def service_pid(controller_pid):
    # Only inspect the controller's immediate children; never match unrelated Java processes.
    for path in Path('/proc').iterdir():
        if not path.name.isdecimal():continue
        pid=int(path.name); details=proc_info(pid)
        if details and details['ppid']==controller_pid:
            try:cmd=(path/'cmdline').read_bytes().replace(b'\0',b' ')
            except OSError:continue
            if b'demo.DeroyqueApplication' in cmd:return pid
    return None

def memory_cgroup():
    p=Path('/sys/fs/cgroup/memory.current')
    try:return int(p.read_text())
    except (OSError,ValueError):return None

def wait_demo(controller,demo_log_path,port,seconds=35):
    url=f'http://127.0.0.1:{port}'
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        if controller.poll() is not None:raise RuntimeError('Demo controller stopped before ready')
        text=demo_log_path.read_text(errors='replace') if demo_log_path.exists() else ''
        match=re.search(r'^Credential file \(chmod 600\): (.+)$',text,re.M)
        if match:
            path=Path(match.group(1).strip())
            if path.is_file():
                data=dict(line.strip().split('=',1) for line in path.read_text().splitlines() if '=' in line)
                if all(k in data for k in ('READ_TOKEN','WRITE_TOKEN')):
                    # Bind readiness to credentials belonging to THIS launch, not a stale
                    # occupant of the port from an earlier service instance.
                    if basic_get(url+'/api/v1/requests/no-such-uuid',data['READ_TOKEN'])==404:
                        return data
        time.sleep(.25)
    raise TimeoutError('Demo startup/authenticated readiness timed out')

def immediate_owned_children(controller_pid):
    result=[]
    for path in Path('/proc').iterdir():
        if not path.name.isdecimal():continue
        pid=int(path.name);details=proc_info(pid)
        if details and details['ppid']==controller_pid:
            try:cmd=(path/'cmdline').read_bytes().replace(b'\0',b' ')
            except OSError:continue
            if (b'demo.DeroyqueApplication' in cmd or b'/provider/independent_provider.py' in cmd) and str(ROOT).encode() in cmd:
                result.append(pid)
    return result

def cleanup(controller,children=()):
    owned=set(children)|set(immediate_owned_children(controller.pid))
    if controller.poll() is None:
        controller.send_signal(signal.SIGINT)
        try:controller.wait(timeout=6)
        except subprocess.TimeoutExpired:
            controller.terminate()
            try:controller.wait(timeout=2)
            except subprocess.TimeoutExpired:controller.kill();controller.wait()
    # dev_demo's own children use start_new_session=True. Verify precise
    # PIDs AND command-line root before trying to reap any survivors.
    for pid in owned:
        path=Path('/proc')/str(pid)/'cmdline'
        try:cmd=path.read_bytes().replace(b'\0',b' ')
        except OSError:continue
        if str(ROOT).encode() not in cmd:continue
        if b'demo.DeroyqueApplication' not in cmd and b'/provider/independent_provider.py' not in cmd:continue
        try:os.kill(pid,signal.SIGTERM)
        except ProcessLookupError:pass
    time.sleep(.2)

def compare(rows):
    def mean(mode,key):return statistics.mean(float(x[key]) for x in rows if x['mode']==mode)
    metrics=['client_qps','client_p95_ms','schedule_lag_p95_ms','service_cpu_seconds_during_load','cgroup_cpu_seconds_during_load']
    result={}
    for key in metrics:
        subset=[r for r in rows if r.get(key) is not None]
        if len(subset)!=len(rows):continue
        off=mean('OFF',key);on=mean('ON',key)
        result[key]={'off_mean':round(off,4),'on_mean':round(on,4),'on_minus_off':round(on-off,4),
                     'relative_percent':round(100*(on-off)/off,3) if off else None}
    return result

def cgroup_cpu():
    try:
        for row in Path('/sys/fs/cgroup/cpu.stat').read_text().splitlines():
            if row.startswith('usage_usec '):return int(row.split()[1])/1e6
    except OSError:pass
    return None

def run_one(a,index,mode,run_label,output,private):
    trial=output/f'{index:02d}-{mode.lower()}';trial.mkdir()
    private_trial=private/f'{index:02d}-{mode.lower()}';private_trial.mkdir(mode=0o700)
    demo_log=(private_trial/'demo.log').open('w');os.chmod(private_trial/'demo.log',0o600)
    env=dict(os.environ,APP_LAB_METRICS_ENABLED='1' if mode=='ON' else '0',
             DEROYQUE_API_PORT=str(a.api_port),DEROYQUE_PROVIDER_PORT=str(a.provider_port))
    controller=subprocess.Popen([sys.executable,'-u',str(ROOT/'scripts/dev_demo.py')],cwd=ROOT,
                                env=env,stdout=demo_log,stderr=subprocess.STDOUT)
    pid=None;pre=None;post=None;sam=[];owned_children=set()
    try:
        creds=wait_demo(controller,private_trial/'demo.log',a.api_port)
        owned_children.update(immediate_owned_children(controller.pid))
        pid=service_pid(controller.pid)
        if pid is None:raise RuntimeError('Cannot locate service JVM child for CPU observation')
        target=f'http://127.0.0.1:{a.api_port}'
        pre=basic_get(target+'/api/v1/lab/ingress-metrics?run='+run_label,creds['READ_TOKEN'])
        if pre!=(200 if mode=='ON' else 404):raise RuntimeError(f'Incorrect opt-in behavior {mode} HTTP {pre}')
        workload_env=dict(env,DEROYQUE_SIM_READ_TOKEN=creds['READ_TOKEN'],DEROYQUE_SIM_WRITE_TOKEN=creds['WRITE_TOKEN'])
        command=[sys.executable,'-u',str(SIM/'run.py'),'--url',target,'--run-id',run_label,
                 '--requests',str(a.requests),'--keys',str(a.keys),'--jvms',str(a.jvms),
                 '--concurrency',str(a.concurrency),'--rate',str(a.rate),
                 '--heap-max-mb',str(a.heap_max_mb),'--start-delay-ms',str(a.start_delay_ms),
                 '--seed','1729','--conflict-percent','25','--distribution','sequential',
                 '--output-dir',str(trial/'load')]
        with (trial/'generator.log').open('w') as genlog:
            generator=subprocess.Popen(command,cwd=ROOT,env=workload_env,stdout=genlog,stderr=subprocess.STDOUT,start_new_session=True)
            before=None;after=None
            while generator.poll() is None:
                proc=proc_info(pid)
                record={'uptime_s':time.monotonic(),'service_cpu_s':proc['cpu_seconds'] if proc else None,
                        'service_rss_bytes':proc['rss_bytes'] if proc else None,
                        'cgroup_cpu_s':cgroup_cpu(),'cgroup_mem_bytes':memory_cgroup()}
                sam.append(record)
                if (trial/'load'/'agent-000.tsv').exists() and before is None:before=record
                time.sleep(a.sample_period)
            rc=generator.wait(timeout=3)
            if rc!=0:raise RuntimeError(f'Load generator exit {rc}; see {trial}/generator.log')
        summary=json.loads((trial/'load'/'summary.json').read_text())
        if summary['outcome']!='PASS' or summary['observed']['unexpected_http_or_transport'] or summary['violations']:
            raise RuntimeError('Load generator reported failure; see summary.json')
        # capture service snapshot only AFTER load (not in timing path)
        snapshot=fetch(target,run_label,creds['READ_TOKEN']) if mode=='ON' else None
        if snapshot:
            (trial/'server-metrics.json').write_text(json.dumps(snapshot,indent=2,sort_keys=True)+'\n')
            statuses=summary['observed']['http_statuses']
            if snapshot['started']!=a.requests or snapshot['finished']!=a.requests:
                raise RuntimeError('Service metrics count mismatch')
            for field,key in [('http_200','200'),('http_202','202'),('http_409','409')]:
                if snapshot[field]!=statuses.get(key,0):raise RuntimeError(f'Service metric {field} differs from client')
        else:
            if basic_get(target+'/api/v1/lab/ingress-metrics?run='+run_label,creds['READ_TOKEN'])!=404:
                raise RuntimeError('Instrumentation unexpectedly visible in OFF trial')
        post=proc_info(pid)
        valid=[s for s in sam if s['service_cpu_s'] is not None and s['cgroup_cpu_s'] is not None]
        first,last=valid[0],valid[-1]
        diff=lambda key:round(last[key]-first[key],4)
        obs=summary['observed']
        result={'trial':index,'mode':mode,'run_id':run_label,'posts':obs['records'],
                'unexpected':obs['unexpected_http_or_transport'],
                'http_statuses':obs['http_statuses'],'peak_client_inflight':obs['peak_inflight_http'],
                'peak_jvms_http':obs['peak_jvms_with_inflight_http'],
                'client_qps':obs['post_attempts_per_second'],'client_p95_ms':obs['latency_ms']['95'],
                'schedule_lag_p95_ms':obs['schedule_lag_ms']['95'],
                'service_cpu_seconds_during_load':diff('service_cpu_s'),
                'cgroup_cpu_seconds_during_load':diff('cgroup_cpu_s'),
                'peak_service_rss_bytes':max(x['service_rss_bytes'] or 0 for x in sam),
                'peak_cgroup_memory_bytes':max(x['cgroup_mem_bytes'] or 0 for x in sam),
                'server_post_qps':(round(snapshot['finished']/((snapshot['last_finish_elapsed_ns']-snapshot['first_start_elapsed_ns'])/1e9),3)
                                   if snapshot and snapshot['last_finish_elapsed_ns']>snapshot['first_start_elapsed_ns'] else None),
                'server_peak_inflight':snapshot['peak_inflight'] if snapshot else None}
        (trial/'telemetry.json').write_text(json.dumps(sam,indent=2)+'\n')
        (trial/'result.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
        return result
    finally:
        cleanup(controller,owned_children)
        demo_log.close()

def write_report(out,rows,diffs):
    lines=['# Deroyque opt-in observability A/B benchmark','',
        'Four trials on ONE SHARED Linux host, separate disposable service schemas. Order: OFF / ON / ON / OFF.',
        'Results are descriptive observations, not a statistically established causal overhead estimate.',
        'Database, broker, background processes, CPU quota and physical resources remain shared.',
        'Service process CPU sampling is approximate; admission QPS is client-observed in both modes.',
        'Each trial uses a separate run ID but the same deterministic key/variant schedule. No JFR was active.', '',
        '| Trial | Metrics | JVMs overlapping HTTP | POSTs | Unexpected | POST/s client | p95 HTTP (ms) | p95 lag (ms) | service CPU seconds* |',
        '|---:|---|---:|---:|---:|---:|---:|---:|---:|']
    for r in rows:lines.append(f"| {r['trial']} | {r['mode']} | {r['peak_jvms_http']} | {r['posts']} | {r['unexpected']} | {r['client_qps']:.2f} | {r['client_p95_ms']:.2f} | {r['schedule_lag_p95_ms']:.2f} | {r['service_cpu_seconds_during_load']:.3f} |")
    lines.extend(['','*Service CPU includes the entire generator process lifetime (including start delay), not exclusively the HTTP admission interval.*','','## Mean ON versus OFF (descriptive)','','| Metric | OFF mean | ON mean | ON minus OFF | Relative change |','|---|---:|---:|---:|---:|'])
    for k,r in diffs.items():lines.append(f"| {k} | {r['off_mean']} | {r['on_mean']} | {r['on_minus_off']} | {r['relative_percent']}% |")
    lines+=['','No statistical significance claim (n=2 per condition).','A/B results can be distorted by PostgreSQL/RabbitMQ activity, producer schedule lag, CPU throttling and warmup.',
      'An uninstrumented service does not expose comparable server-internal QPS; do NOT interpret missing OFF server QPS as zero.',
      'No changes were made to production or shared database/broker service configuration.','']
    (out/'COMPARISON.md').write_text('\n'.join(lines))

def main(argv=None):
    a=parse_args(argv)
    a.output_dir.mkdir(parents=True,exist_ok=False)
    a.private_dir.mkdir(parents=True,exist_ok=True);a.private_dir.chmod(0o700)
    run_key='ab'+uuid.uuid4().hex[:9]
    rows=[]
    for i,mode in enumerate(ORDER,1):
        tag=f'{run_key}-{i}'
        print(f'ARM {i}/4 {mode}: {a.jvms} JVMs; {a.requests} requests; target {a.rate} POST/s',flush=True)
        row=run_one(a,i,mode,tag,a.output_dir,a.private_dir)
        rows.append(row)
        print(f"ARM {i}/4 PASS: 0/{row['posts']} unexpected; {row['posts']}/{row['posts']} expected; {row['client_qps']} req/s",flush=True)
    diffs=compare(rows)
    data={'schema_version':1,'order':ORDER,'parameters':{'jvms':a.jvms,'concurrency':a.concurrency,
          'requests_per_trial':a.requests,'keys':a.keys,'rate':a.rate,'heap_max_mb':a.heap_max_mb},
          'trials':rows,'comparison':diffs,
          'limitation':'Single Linux, shared CPU quota, two observations per mode; not a production overhead estimate.'}
    (a.output_dir/'COMPARISON.json').write_text(json.dumps(data,indent=2,sort_keys=True)+'\n')
    write_report(a.output_dir,rows,diffs)
    print('ABBA_COMPLETE 4/4 arms successful, results=',a.output_dir,flush=True)
    return 0

if __name__=='__main__':
    try:sys.exit(main())
    except (Exception,KeyboardInterrupt) as e:
        print('ABBA_FAILED:',str(e),file=sys.stderr)
        sys.exit(1)
