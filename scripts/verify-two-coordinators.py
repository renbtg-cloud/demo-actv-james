#!/usr/bin/env python3
"""Two coordinator integration against the real Deroyque, PostgreSQL, RabbitMQ and mock.

Both coordinators run on the same Linux. Only loopback targets permitted.
Credential files stay outside results. Never stops shared PG/Rabbit infrastructure.
"""
import argparse
import json
import os
import pathlib
import secrets
import subprocess
import sys
import time
import urllib.parse

ROOT=pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'simulator'))
from distributed_merge import merge
from server_metrics import fetch


def run(args):
    url=urllib.parse.urlsplit(args.url)
    if url.scheme!='http' or url.hostname not in ('127.0.0.1','localhost','::1') or url.path not in ('','/') or url.query or url.fragment:
        raise ValueError('This laboratory runner accepts loopback HTTP only')
    if args.output.exists():raise ValueError('Output exists; refusing overwrite')
    if args.posts<args.keys or args.posts<2*args.jvms:raise ValueError('Insufficient POSTs/keys')
    creds=dict(line.strip().split('=',1) for line in args.credentials.read_text().splitlines() if '=' in line)
    if not creds.get('READ_TOKEN') or not creds.get('WRITE_TOKEN'):
        raise ValueError('Expected READ_TOKEN and WRITE_TOKEN in disposable demo credentials')
    args.output.mkdir(parents=True)
    env={**os.environ,'DEROYQUE_SIM_WRITE_TOKEN':creds['WRITE_TOKEN'],'DEROYQUE_SIM_READ_TOKEN':creds['READ_TOKEN']}
    run_id='m8-'+secrets.token_hex(6)
    barrier=int(time.time()*1000)+12000
    processes=[]
    try:
        for shard in range(2):
            folder=args.output/f'host-{shard}'
            command=[sys.executable,str(ROOT/'simulator/run.py'),'--url',args.url,'--jvms',str(args.jvms),
                '--concurrency','6','--requests',str(args.posts),'--keys',str(args.keys),
                '--rate',str(args.rate),'--conflict-percent','35','--distribution','hotspot',
                '--shard-count','2','--shard-index',str(shard),'--heap-min-mb','16',
                '--heap-max-mb','32','--active-processors','2','--start-at-ms',str(barrier),
                '--run-id',run_id,'--output-dir',str(folder)]
            log=(args.output/f'coordinator-{shard}.log').open('w')
            p=subprocess.Popen(command,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            processes.append((p,log))
        for i,(p,log) in enumerate(processes):
            try:p.wait(timeout=90)
            finally:log.close()
            if p.returncode!=0:
                raise RuntimeError(f'Coordinator {i} exit={p.returncode}; inspect coordinator-{i}.log')
        snapshot=fetch(args.url,run_id,creds['READ_TOKEN'])
        (args.output/'service-metrics.json').write_text(json.dumps(snapshot,indent=2,sort_keys=True)+'\n')
        report=merge([args.output/'host-0',args.output/'host-1'],verify_state=True,
            await_done_seconds=args.await_done,token=creds['READ_TOKEN'],server_metrics=snapshot)
        (args.output/'merged.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
        summary={k:report.get(k) for k in ('outcome','global_recorded_posts','unexpected_http_or_transport','http_statuses',
                    'global_invariant_violations','harness_errors','state_verification_errors','verified_states',
                    'total_producer_jvms','global_throughput_qps','server_side')}
        print(json.dumps(summary,indent=2),flush=True)
        return 0 if report['outcome']=='PASS' else 1
    finally:
        for p,log in processes:
            if p.poll() is None:
                p.terminate()
                try:p.wait(timeout=3)
                except subprocess.TimeoutExpired:p.kill();p.wait()
            if not log.closed:log.close()

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',default='http://127.0.0.1:18081')
    parser.add_argument('--credentials',required=True,type=pathlib.Path)
    parser.add_argument('--output',required=True,type=pathlib.Path)
    parser.add_argument('--jvms',type=int,default=4,help='JVMs per coordinator')
    parser.add_argument('--posts',type=int,default=320)
    parser.add_argument('--keys',type=int,default=80)
    parser.add_argument('--rate',type=float,default=100)
    parser.add_argument('--await-done',type=float,default=60)
    args=parser.parse_args()
    try:sys.exit(run(args))
    except (ValueError,RuntimeError,OSError,KeyError) as e:
        print('TWO_COORDINATOR_LAB_FAILED',e,file=sys.stderr);sys.exit(2)
