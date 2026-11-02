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
from host_provenance import evaluate_provenance
from witness_audit import verify_witness

PLAN_FIELDS=('profile','distribution','duration_seconds','seed','global_attempted_posts',
             'shared_keys','conflict_percent','target_qps','shard_count','start_at_ms',
             'ramp_start_rate','burst_rate','burst_width_seconds','burst_period_seconds',
             'hotspot_percent','hotspot_keys_percent','skew_power')

def _metrics_object(source):
    if isinstance(source, (str, pathlib.Path)):
        return json.loads(pathlib.Path(source).read_text(encoding='utf-8'))
    return source


def audit_service_snapshots(sources, run_id, records, statuses):
    """Validate independent per-JVM counters; never sum unaligned monotonic clocks."""
    if not isinstance(sources, (tuple, list)):
        sources = [sources]
    errors = []
    if not sources:
        return None, ['No service metrics snapshots supplied']
    required = ('schema_version', 'instance_id', 'run_id', 'server_elapsed_ns',
                'started', 'finished', 'inflight', 'peak_inflight',
                'first_start_elapsed_ns', 'last_finish_elapsed_ns',
                'http_202', 'http_200', 'http_409', 'http_other', 'response_incomplete')
    seen = set()
    per_instance = []
    totals = {name: 0 for name in ('started', 'finished', 'http_202', 'http_200', 'http_409')}
    for position, item in enumerate(sources):
        try:
            source = _metrics_object(item)
            if not isinstance(source, dict) or any(k not in source for k in required):
                raise ValueError('Missing required fields')
            instance = source['instance_id']
            if (source['schema_version'] != 1 or type(source['schema_version']) is not int or
                    source['run_id'] != run_id or not isinstance(instance, str) or not instance):
                raise ValueError('Run identity, schema or instance ID mismatch')
            if instance in seen:
                raise ValueError('Duplicate service instance ID')
            seen.add(instance)
            numeric = [k for k in required if k not in ('instance_id', 'run_id')]
            if any(type(source[k]) is not int or source[k] < 0 for k in numeric):
                raise ValueError('Negative, boolean or non-integer counter')
            if source['started'] <= 0 or source['finished'] != source['started'] or source['inflight'] != 0:
                raise ValueError('Service counts disagree: incomplete admission')
            if source['http_other'] or source['response_incomplete']:
                raise ValueError('Unexpected HTTP status or incomplete response')
            if source['started'] != sum(source['http_'+c] for c in ('202', '200', '409')):
                raise ValueError('Service status counters do not sum to started')
            first, last = source['first_start_elapsed_ns'], source['last_finish_elapsed_ns']
            if first <= 0 or last <= first or source['server_elapsed_ns'] < last:
                raise ValueError('Invalid single-instance monotonic interval')
            if not 1 <= source['peak_inflight'] <= source['started']:
                raise ValueError('Invalid single-instance peak in-flight')
            duration = (last - first)/1e9
            per_instance.append({'instance_id': instance, 'finished': source['finished'],
                'local_window_seconds': round(duration, 6),
                'local_post_qps': round(source['finished']/duration, 3),
                'peak_inflight': source['peak_inflight']})
            for name in totals:
                totals[name] += source[name]
        except (ValueError, TypeError, KeyError, OSError, json.JSONDecodeError) as exc:
            errors.append(f'snapshot {position}: {exc}')
    if not errors:
        if totals['started'] != len(records) or totals['finished'] != len(records):
            errors.append('Service POST counts disagree with complete producer evidence')
        for name, status in (('http_202', '202'), ('http_200', '200'), ('http_409', '409')):
            if totals[name] != int(statuses.get(status, 0)):
                errors.append(f'Service {name} count disagrees with producer HTTP statuses')
    if errors:
        return None, errors
    if len(per_instance) == 1:
        x = per_instance[0]
        result = {'instance_id': x['instance_id'], 'scope': 'one_service_jvm_http_handler',
            'server_window_seconds': x['local_window_seconds'],
            'server_post_qps': x['local_post_qps'],
            'server_handler_peak_inflight': x['peak_inflight'],
            'measurement_note': 'Single JVM monotonic handler window, not provider completion throughput.'}
    else:
        result = {'scope': 'multiple_service_jvms_no_shared_monotonic_clock',
            'instance_count': len(per_instance), 'total_finished': totals['finished'],
            'per_instance': sorted(per_instance, key=lambda x: x['instance_id']),
            'server_post_qps': None, 'server_handler_peak_inflight': None,
            'measurement_note': 'Each instance has an independent monotonic window. Their QPS and peaks must NOT be summed; global elapsed time and simultaneous concurrency remain unknown. All active service instances must be included.'}
    return result, []


def merge(folders,verify_state=False,await_done_seconds=0,timeout_ms=8000,token=None,server_metrics=None,require_distinct_kernels=False,gateway_witness=None):
    if not folders:raise ValueError('Supply every shard output directory')
    chunks=[];all_records=[];seen_shards=set(); errors=[];plan=None;run_id=None;target=None
    host_snapshots=[]
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
        host_snapshots.append(data.get('host_environment'))
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
        server_report, mismatch = audit_service_snapshots(server_metrics, run_id, all_records, statuses)
        errors.extend(['service metrics: '+v for v in mismatch])
    gateway_report = None
    if gateway_witness is not None:
        gateway_report, witness_errors = verify_witness(gateway_witness, run_id, all_records, statuses)
        errors.extend(["gateway witness: " + e for e in witness_errors])
    host_evidence = evaluate_provenance(host_snapshots, run_id)
    if require_distinct_kernels and host_evidence['status'] != 'DISTINCT_KERNEL_REPORTS_UNVERIFIED':
        errors.append('independent-kernel evidence required but not established: '+host_evidence['status'])
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
        'host_evidence':host_evidence,
        'global_throughput_qps':(gateway_report['gateway_completed_post_qps'] if gateway_report else
                                server_report['server_post_qps'] if server_report else None),
        'gateway_witness':gateway_report,
        'server_side':server_report,
        'global_peak_http_concurrency':None,
        'measurement_note':('Gateway HTTP completion QPS uses one gateway monotonic clock across backend JVMs (proxy overhead included); NOT provider effects.' if gateway_report else
            'Global HTTP QPS is provided only for a fully reconciled single service JVM. Multi-JVM monotonic clock windows cannot be summed; cross-host client peak concurrency remains unmeasured.'),
        'outcome':'FAIL' if errors or violations or state_errors or error_http else 'PASS'}
    return result

def main(argv=None):
    parser=argparse.ArgumentParser(description='Merge all host shards and audit global idempotency')
    parser.add_argument('folders',nargs='+',type=pathlib.Path)
    parser.add_argument('--output',type=pathlib.Path)
    parser.add_argument('--verify-state',action='store_true')
    parser.add_argument('--await-done-seconds',type=float,default=0)
    parser.add_argument('--server-metrics',type=pathlib.Path,action='append',help='One metrics snapshot per active service JVM, captured AFTER all shards finish (repeat)')
    parser.add_argument('--gateway-witness',type=pathlib.Path,help='Optional final loopback gateway witness JSON from ONE monotonic clock')
    parser.add_argument('--require-distinct-kernels',action='store_true',help='Fail if independent reported kernel fingerprints are not demonstrated for the producer shards')
    p=parser.parse_args(argv)
    import os
    try:r=merge(p.folders,p.verify_state,p.await_done_seconds,token=os.environ.get('DEROYQUE_SIM_READ_TOKEN'),server_metrics=p.server_metrics,require_distinct_kernels=p.require_distinct_kernels,gateway_witness=p.gateway_witness)
    except (ValueError,KeyError,OSError,json.JSONDecodeError) as e:
        print(f'DISTRIBUTED_MERGE_FATAL: {e}',file=sys.stderr);return 2
    if p.output:
        if p.output.exists():print('Output file already exists',file=sys.stderr);return 2
        p.output.parent.mkdir(parents=True,exist_ok=True)
        p.output.write_text(json.dumps(r,indent=2,sort_keys=True)+'\n')
    print(json.dumps(r,indent=2,sort_keys=True))
    return 0 if r['outcome']=='PASS' else 1

if __name__=='__main__':sys.exit(main())
