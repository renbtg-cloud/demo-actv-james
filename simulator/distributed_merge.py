#!/usr/bin/env python3
"""Aggregate independent host shards, then audit global POST idempotency.

Use an *offline trusted* collection of complete shard outputs. Authentication or
clock synchronization between machines is deliberately NOT inferred from this tool.
"""
import argparse
import collections
import json
import pathlib
import sys
from run import audit,load_records,duration_ms_to_percentile,verify
from workload import create_plan
from types import SimpleNamespace

PLAN_FIELDS=('profile','distribution','duration_seconds','seed','global_attempted_posts',
             'shared_keys','conflict_percent','target_qps','shard_count','start_at_ms',
             'ramp_start_rate','burst_rate','burst_width_seconds','burst_period_seconds',
             'hotspot_percent','hotspot_keys_percent','skew_power')

def merge(folders,verify_state=False,await_done_seconds=0,timeout_ms=8000,token=None,server_metrics=None):
    if not folders:raise ValueError('Supply every shard output directory')
    chunks=[];all_records=[];seen_shards=set(); errors=[];plan=None;run_id=None;target=None
    for folder in folders:
        summary_file=pathlib.Path(folder)/'summary.json'
        data=json.loads(summary_file.read_text(encoding='utf-8'))
        p=data['plan']; index=p['shard_index']; total=p['shard_count']; size=p['global_attempted_posts']
        if not isinstance(index,int) or not isinstance(total,int) or not (0<=index<total<=256):
            raise ValueError(f'Invalid shard index/count in {folder}')
        if index in seen_shards:raise ValueError(f'Duplicate shard index {index}')
        seen_shards.add(index)
        if plan is None:
            plan=p;run_id=data['run_id'];target=data['target']
        elif (data['run_id']!=run_id or data['target']!=target or
              any(p.get(key)!=plan.get(key) for key in PLAN_FIELDS)):
            raise ValueError(f'Incompatible shard run identity or workload in {folder}')
        expected=set(range(index,size,total))
        local,read_errors=load_records(pathlib.Path(folder),p['jvms'],size,expected)
        errors.extend([f'shard {index}: {e}' for e in read_errors])
        if len(local)!=p['local_attempted_posts'] or len(local)!=data['observed']['records']:
            errors.append(f'shard {index}: record count disagrees with summaries')
        if data['outcome'] not in ('SHARD_PASS_GLOBAL_AUDIT_PENDING','PASS'):
            errors.append(f'shard {index}: local outcome is {data["outcome"]}')
        if data.get('violations') or data.get('harness_errors') or data.get('state_verification_errors'):
            errors.append(f'shard {index}: local diagnostic failures in summary')
        for record in local:
            record['agent']=f'{index}/{record["agent"]}'
        all_records.extend(local)
        chunks.append({'shard':index,'jvms':p['jvms'],
                       'records':len(local),'local_qps':data['observed'].get('post_attempts_per_second'),
                       'max_local_inflight':data['observed'].get('peak_inflight_http'),
                       'max_local_active_jvms':data['observed'].get('peak_jvms_with_inflight_http'),
                       'outcome':data['outcome']})
    if seen_shards!=set(range(plan['shard_count'])):
        errors.append(f'Missing shard indices: {sorted(set(range(plan["shard_count"]))-seen_shards)}')
    seqs=[r['sequence'] for r in all_records]
    if len(set(seqs))!=plan['global_attempted_posts'] or len(seqs)!=plan['global_attempted_posts']:
        errors.append('Global sequence coverage or uniqueness mismatch')
    # Regenerate the exact deterministic schedule and check that returned records
    # correspond to their global sequence/identity, not just HTTP status classes.
    regenerated=SimpleNamespace(requests=plan['global_attempted_posts'],keys=plan['shared_keys'],
        rate=plan['target_qps'],conflict_percent=plan['conflict_percent'],
        profile=plan['profile'],distribution=plan['distribution'],duration_seconds=plan['duration_seconds'],
        seed=plan['seed'],ramp_start_rate=plan['ramp_start_rate'],burst_rate=plan['burst_rate'],
        burst_width_seconds=plan['burst_width_seconds'],burst_period_seconds=plan['burst_period_seconds'],
        hotspot_percent=plan['hotspot_percent'],hotspot_keys_percent=plan['hotspot_keys_percent'],
        skew_power=plan['skew_power'])
    actual_by_sequence={r['sequence']:r for r in all_records}
    for seq,slot,variant,_offset in create_plan(regenerated):
        item=actual_by_sequence.get(seq)
        if item is not None and (item['slot']!=slot or item['variant']!=variant):
            errors.append(f'Global plan mismatch at sequence {seq}')
            if len(errors)>100:break
    violations,winner,ids,statuses=audit(all_records,plan['shared_keys'])
    error_http=sum(r['status'] not in (200,202,409) for r in all_records)
    state_errors=[];states={}
    if verify_state and not errors and not violations:
        if not token:raise ValueError('DEROYQUE_SIM_READ_TOKEN required to verify server state')
        state_errors,states=verify(target,token,run_id,winner,ids,timeout_ms,await_done_seconds)
    latencies=[r['latency_ns'] for r in all_records if r['status']>=0]
    server_report=None
    if server_metrics is not None:
        source=json.loads(pathlib.Path(server_metrics).read_text(encoding='utf-8')) if isinstance(server_metrics,(str,pathlib.Path)) else server_metrics
        mismatch=[]
        required=('schema_version','instance_id','run_id','server_elapsed_ns','started','finished','inflight',
                  'peak_inflight','first_start_elapsed_ns','last_finish_elapsed_ns','http_202','http_200',
                  'http_409','http_other','response_incomplete')
        if any(k not in source for k in required):
            mismatch.append('Missing required server metrics fields')
        elif source['schema_version']!=1 or source['run_id']!=run_id or not isinstance(source['instance_id'],str) or not source['instance_id']:
            mismatch.append('Server metrics identity or schema mismatch')
        else:
            values=[source[k] for k in required if k not in ('instance_id','run_id')]
            if any(type(v) is not int or v<0 for v in values):mismatch.append('Negative or non-integer server measurement')
            if source['started']!=len(all_records) or source['finished']!=len(all_records) or source['inflight']!=0:
                mismatch.append('Service POST counts disagree with complete producer evidence')
            for key,label in [('http_202','202'),('http_200','200'),('http_409','409')]:
                if source[key]!=int(statuses.get(label,0)):
                    mismatch.append(f'Service {key} count disagrees with producer HTTP statuses')
            if source['http_other'] or source['response_incomplete']:
                mismatch.append('Unexpected service statuses or incomplete response writes')
            if source['first_start_elapsed_ns']<=0 or source['last_finish_elapsed_ns']<=source['first_start_elapsed_ns'] or source['server_elapsed_ns']<source['last_finish_elapsed_ns']:
                mismatch.append('Invalid or incomplete service monotonic measurement interval')
            if not 1<=source['peak_inflight']<=source['started']:
                mismatch.append('Invalid service handler concurrency peak')
        if mismatch:errors.extend(['service metrics: '+v for v in mismatch])
        else:
            elapsed=(source['last_finish_elapsed_ns']-source['first_start_elapsed_ns'])/1e9
            server_report={'instance_id':source['instance_id'],'scope':'one_service_jvm_http_handler',
                'server_window_seconds':elapsed,'server_post_qps':round(source['finished']/elapsed,3),
                'server_handler_peak_inflight':source['peak_inflight'],
                'measurement_note':'Single JVM monotonic handler window, not total network in-flight concurrency or provider-completion throughput. Run-tagged laboratory requests only.'}
    result={'run_id':run_id,'target':target,'shard_count':plan['shard_count'],
        'shards_received':len(seen_shards),'global_requested_posts':plan['global_attempted_posts'],
        'global_recorded_posts':len(all_records),'global_keys':plan['shared_keys'],
        'total_producer_jvms':sum(c['jvms'] for c in chunks),
        'unexpected_http_or_transport':error_http,
        'http_statuses':statuses,
        'latency_ms':{str(q):duration_ms_to_percentile(latencies,q) for q in (50,90,95,99)},
        'state_verification_errors':state_errors,'verified_states':states,
        'global_invariant_violations':violations,'harness_errors':errors,
        'per_shard':sorted(chunks,key=lambda c:c['shard']),
        'global_throughput_qps':server_report['server_post_qps'] if server_report else None,
        'server_side':server_report,
        'global_peak_http_concurrency':None,
        'measurement_note':'Service-side JVM monotonic QPS is used only with a validated complete run-tagged service snapshot. Cross-host client peak concurrency remains unmeasured; otherwise throughput is unset.',
        'outcome':'FAIL' if errors or violations or state_errors or error_http else 'PASS'}
    return result

def main(argv=None):
    parser=argparse.ArgumentParser(description='Merge all host shards and audit global idempotency')
    parser.add_argument('folders',nargs='+',type=pathlib.Path)
    parser.add_argument('--output',type=pathlib.Path)
    parser.add_argument('--verify-state',action='store_true')
    parser.add_argument('--await-done-seconds',type=float,default=0)
    parser.add_argument('--server-metrics',type=pathlib.Path,help='single-JVM lab metrics snapshot captured AFTER all shards finish')
    p=parser.parse_args(argv)
    import os
    try:r=merge(p.folders,p.verify_state,p.await_done_seconds,token=os.environ.get('DEROYQUE_SIM_READ_TOKEN'),server_metrics=p.server_metrics)
    except (ValueError,KeyError,OSError,json.JSONDecodeError) as e:
        print(f'DISTRIBUTED_MERGE_FATAL: {e}',file=sys.stderr);return 2
    if p.output:
        if p.output.exists():print('Output file already exists',file=sys.stderr);return 2
        p.output.parent.mkdir(parents=True,exist_ok=True)
        p.output.write_text(json.dumps(r,indent=2,sort_keys=True)+'\n')
    print(json.dumps(r,indent=2,sort_keys=True))
    return 0 if r['outcome']=='PASS' else 1

if __name__=='__main__':sys.exit(main())
