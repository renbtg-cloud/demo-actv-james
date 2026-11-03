#!/usr/bin/env python3
"""Aggregate independent host shards, then audit global POST idempotency.

Use an *offline trusted* collection of complete shard outputs. Authentication or
clock synchronization between machines is deliberately NOT inferred from this tool.
"""
import argparse
import collections
import hashlib
import json
import pathlib
import sys
from run import audit,load_records,duration_ms_to_percentile,verify
from workload import create_plan
from types import SimpleNamespace
from host_provenance import evaluate_provenance
from witness_audit import verify_witness
from distributed_integrity import verify_launch, verify_seal, ordered_shard_folders
from clock_bounds import audit_clock_probes

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


def merge(folders,verify_state=False,await_done_seconds=0,timeout_ms=8000,token=None,server_metrics=None,require_distinct_kernels=False,gateway_witness=None,launch_manifest=None,evidence_index=None,clock_evidence=None,max_clock_width_ms=100,host_pins=None,signed_host_reports=None,host_session=None,signed_clock_reports=None,readiness_matrix=None,readiness_reports=None,signed_readiness=None,preflight_checkpoint=None,collector_public_key=None):
    if not folders:raise ValueError('Supply every shard output directory')
    # Input order is not a host identifier. Canonicalize before reading per-host
    # records, provenance or positional evidence digests.
    if len(folders) >= 2:
        folders = ordered_shard_folders(folders)
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
    if launch_manifest is not None:
        summaries=[json.loads((pathlib.Path(folder)/'summary.json').read_text(encoding='utf-8')) for folder in folders]
        # Strict launch binding uses index-ordered shard summaries, not user path order.
        indexed={s['plan']['shard_index']:s for s in summaries}
        if set(indexed)!=set(range(plan['shard_count'])):
            errors.append('launch binding requires complete shard indices')
        else:
            errors.extend('launch manifest: '+error for error in
                          verify_launch(launch_manifest,[indexed[i] for i in range(plan['shard_count'])]))
    if evidence_index is not None:
        errors.extend('sealed evidence: '+error for error in verify_seal(evidence_index,folders))
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
    clock_report = None
    if clock_evidence is not None:
        try:
            path = pathlib.Path(clock_evidence)
            if path.stat().st_size > 2 * 1024 * 1024:
                raise ValueError('Oversized clock evidence')
            document = json.loads(path.read_text(encoding='utf-8'))
            clock_report, clock_errors = audit_clock_probes(document,run_id,plan['shard_count'],max_clock_width_ms)
            errors.extend('clock evidence: '+err for err in clock_errors)
        except (ValueError,KeyError,TypeError,OSError,json.JSONDecodeError) as exc:
            errors.append('clock evidence: '+str(exc))
    # A valid signature on individual clock reports is not enough if the
    # subsequently aggregated clock evidence contains *different samples*.
    # Tie the aggregate used for timing analysis to exactly those signed bytes.
    if signed_clock_reports is not None:
        if clock_evidence is None:
            errors.append('signed clock reports require aggregate clock evidence')
        else:
            try:
                from host_session import read_json
                aggregate=read_json(clock_evidence,2*1024*1024)
                entries=aggregate.get('shards')
                if (not isinstance(entries,list) or len(entries)!=plan['shard_count'] or
                    aggregate.get('run_id')!=run_id):
                    raise ValueError('Incomplete signed clock aggregation')
                by_shard={}
                for clock_path in signed_clock_reports:
                    source=read_json(clock_path,512*1024)
                    shard=source.get('shard_index')
                    if type(shard) is not int or shard in by_shard or not 0<=shard<plan['shard_count']:
                        raise ValueError('Invalid/duplicate signed clock report shard')
                    by_shard[shard]=source
                if set(by_shard)!=set(range(plan['shard_count'])):
                    raise ValueError('Missing signed clock report shard')
                for i,entry in enumerate(entries):
                    source=by_shard[i]
                    if (not isinstance(entry,dict) or type(entry.get('shard_index')) is not int or
                        entry['shard_index']!=i or entry.get('samples')!=source.get('samples') or
                        source.get('reference_id')!=aggregate.get('reference_id') or
                        source.get('run_id')!=run_id):
                        raise ValueError('Clock aggregate contradicts signed shard report')
            except (ValueError,TypeError,KeyError,OSError,json.JSONDecodeError) as exc:
                errors.append('signed clock aggregation: '+str(exc))
    signed_host_report = None
    if host_session is not None and (launch_manifest is None or host_pins is None or signed_host_reports is None):
        errors.append('Collector session requires launch manifest, host pins and signed reports')
    if signed_clock_reports is not None and host_session is None:
        errors.append('Signed clock evidence requires collector session')
    if (host_pins is None) != (signed_host_reports is None):
        errors.append('signed host evidence requires BOTH pinned public keys and all signed reports')
    elif host_pins is not None:
        try:
            from host_signed_evidence import verify_envelopes
            pins_path=pathlib.Path(host_pins)
            if pins_path.is_symlink() or pins_path.stat().st_size > 128*1024:
                raise ValueError('Unsafe or oversized pinned-key inventory')
            pins=json.loads(pins_path.read_text(encoding='utf-8'))
            from host_session import read_json
            session=read_json(host_session) if host_session is not None else None
            manifest=read_json(launch_manifest,2*1024*1024) if host_session is not None else None
            signed_host_report, signature_errors=verify_envelopes(
                pins,signed_host_reports,folders,run_id,session=session,manifest=manifest,
                clock_reports=signed_clock_reports)
            errors.extend('signed host evidence: '+err for err in signature_errors)
        except (ValueError,TypeError,KeyError,OSError,json.JSONDecodeError) as exc:
            errors.append('signed host evidence: '+str(exc))
    readiness_handoff = None
    if any(v is not None for v in (readiness_matrix,readiness_reports,signed_readiness)):
        if any(v is None for v in (readiness_matrix,readiness_reports,signed_readiness,
                                   launch_manifest,host_pins,host_session,signed_host_reports)):
            errors.append('readiness handoff requires COMPLETE preflight and signed post-run inputs')
        else:
            try:
                from readiness_handoff import verify_handoff
                from host_session import read_json
                preflight_manifest=read_json(launch_manifest,2*1024*1024) if isinstance(launch_manifest,(str,pathlib.Path)) else launch_manifest
                pinned=read_json(host_pins) if isinstance(host_pins,(str,pathlib.Path)) else host_pins
                signing_session=read_json(host_session) if isinstance(host_session,(str,pathlib.Path)) else host_session
                readiness_handoff=verify_handoff(readiness_matrix,readiness_reports,signed_readiness,
                                                   preflight_manifest,pinned,signing_session,
                                                   signed_host_report,host_snapshots)
            except (ValueError,TypeError,KeyError,OSError,UnicodeError,json.JSONDecodeError) as exc:
                errors.append('signed readiness handoff: '+str(exc))
    if preflight_checkpoint is not None or collector_public_key is not None:
        if (preflight_checkpoint is None or collector_public_key is None or
            readiness_matrix is None or readiness_reports is None or signed_readiness is None or
            launch_manifest is None or host_pins is None or host_session is None or
            readiness_handoff is None):
            errors.append('Collector checkpoint requires COMPLETE verified two-phase handoff and pinned collector key')
        else:
            try:
                from readiness_checkpoint import verify_checkpoint
                from host_session import read_json
                manifest_doc=read_json(launch_manifest,2*1024*1024) if isinstance(launch_manifest,(str,pathlib.Path)) else launch_manifest
                pins_doc=read_json(host_pins) if isinstance(host_pins,(str,pathlib.Path)) else host_pins
                session_doc=read_json(host_session) if isinstance(host_session,(str,pathlib.Path)) else host_session
                checkpoint=verify_checkpoint(readiness_matrix,preflight_checkpoint,collector_public_key,
                                              readiness_reports,signed_readiness,session_doc,manifest_doc,pins_doc)
                if checkpoint['matrix_sha256'] != hashlib.sha256(__import__('host_readiness_signature')._read_regular(readiness_matrix,2*1024*1024)[0]).hexdigest():
                    raise ValueError('Collector checkpoint matrix bytes changed after verification')
                readiness_handoff['collector_checkpoint']=checkpoint
            except (ValueError,TypeError,KeyError,OSError,UnicodeError,json.JSONDecodeError) as exc:
                errors.append('collector checkpoint: '+str(exc))
    # Stable workload identity across repetitions (exclude absolute UTC launch barrier).
    # This does not by itself prove identical hardware or runtime configuration.
    workload_spec={k:plan.get(k) for k in PLAN_FIELDS if k!='start_at_ms'}
    signature=hashlib.sha256(json.dumps(workload_spec,sort_keys=True,separators=(',',':'),
                                       allow_nan=False).encode('utf-8')).hexdigest()
    result={'session_sha256':(__import__('host_session').digest(__import__('host_session').read_json(host_session)) if host_session is not None and signed_host_report is not None else None), 'run_id':run_id,'target':target,'shard_count':plan['shard_count'],
        'workload_sha256':signature,
        'shards_received':len(seen_shards),'global_requested_posts':plan['global_attempted_posts'],
        'global_recorded_posts':len(all_records),'global_keys':plan['shared_keys'],
        'total_producer_jvms':sum(c['jvms'] for c in chunks),
        'unexpected_http_or_transport':error_http,
        'http_statuses':statuses,
        'latency_ms':{str(q):duration_ms_to_percentile(latencies,q) for q in (50,90,95,99)},
        'state_verification_errors':state_errors,'verified_states':states,
        'winning_variants':{str(slot):variant for slot,variant in sorted(winner.items())},
        'global_invariant_violations':violations,'harness_errors':errors,
        'per_shard':sorted(chunks,key=lambda c:c['shard']),
        'host_evidence':host_evidence, 'clock_evidence':clock_report,
        'signed_host_evidence':signed_host_report,
        'readiness_handoff':readiness_handoff,
        'global_throughput_qps':(gateway_report['gateway_completed_post_qps'] if gateway_report else
                                server_report['server_post_qps'] if server_report else None),
        'gateway_witness':gateway_report,
        'server_side':server_report,
        'global_peak_http_concurrency':None,
        'measurement_note':('Gateway HTTP completion QPS uses one gateway monotonic clock across backend JVMs (proxy overhead included); NOT provider effects.' if gateway_report else
            'Global HTTP QPS is provided only for a fully reconciled single service JVM. Multi-JVM monotonic clock windows cannot be summed; cross-host client peak concurrency remains unmeasured.'),
        'outcome':'FAIL' if errors or violations or state_errors or error_http else 'PASS'}
    return result

def strict_external_preflight(launch_manifest=None, evidence_index=None, clock_evidence=None,
                              require_distinct_kernels=False):
    """Require the three independent collection inputs, never attest host hardware.

    Even a full pass is *collector evidence only*: untrusted producers can forge
    self-reported host/kernel fields, and SHA-256 does not authenticate authors.
    """
    errors = []
    if launch_manifest is None:
        errors.append('Missing independently retained launch manifest')
    if evidence_index is None:
        errors.append('Missing independently retained shard SHA256 evidence index')
    if clock_evidence is None:
        errors.append('Missing collected clock offset evidence')
    if not require_distinct_kernels:
        errors.append('Distinct reported kernel check must be explicitly required')
    return errors


def main(argv=None):
    parser=argparse.ArgumentParser(description='Merge all host shards and audit global idempotency')
    parser.add_argument('folders',nargs='+',type=pathlib.Path)
    parser.add_argument('--output',type=pathlib.Path)
    parser.add_argument('--verify-state',action='store_true')
    parser.add_argument('--await-done-seconds',type=float,default=0)
    parser.add_argument('--server-metrics',type=pathlib.Path,action='append',help='One metrics snapshot per active service JVM, captured AFTER all shards finish (repeat)')
    parser.add_argument('--gateway-witness',type=pathlib.Path,help='Optional final loopback gateway witness JSON from ONE monotonic clock')
    parser.add_argument('--require-distinct-kernels',action='store_true',help='Fail if independent reported kernel fingerprints are not demonstrated for the producer shards')
    parser.add_argument('--launch-manifest',type=pathlib.Path,help='Collector-retained v2 launch manifest; fail closed on mismatched settings')
    parser.add_argument('--evidence-index',type=pathlib.Path,help='Collector-retained SHA256 index, sealed before merge')
    parser.add_argument('--clock-evidence',type=pathlib.Path,help='Collector-retained four-timestamp UTC offset observations (not host proof)')
    parser.add_argument('--max-clock-width-ms',type=float,default=100.0)
    parser.add_argument('--host-pins',type=pathlib.Path,help='Collector-pinned JSON host->Ed25519 public key inventory')
    parser.add_argument('--signed-host-report',action='append',type=pathlib.Path,help='One signed report per positional shard directory')
    parser.add_argument('--host-session',type=pathlib.Path,help='Collector-held, unpredictable run challenge that binds launch manifest, pinned keys and host assignments')
    parser.add_argument('--signed-clock-report',action='append',type=pathlib.Path,help='Optional per-shard clock probe bound to Ed25519 report; requires host session')
    parser.add_argument('--require-host-session',action='store_true',help='Refuse reports without collector session, launch manifest and pinned keys')
    parser.add_argument('--require-host-signatures',action='store_true',help='Fail closed unless signed reports and a pinned public-key inventory were supplied')
    parser.add_argument('--readiness-matrix',type=pathlib.Path,help='Trusted collector-saved signed preflight verdict; requires exact reports, proofs and postrun host signatures')
    parser.add_argument('--readiness-report',type=pathlib.Path,action='append',help='One unchanged preflight JSON per assigned host')
    parser.add_argument('--signed-readiness',type=pathlib.Path,action='append',help='One session-bound Ed25519 preflight proof per host')
    parser.add_argument('--preflight-checkpoint',type=pathlib.Path,help='Optional Ed25519-signed collector checkpoint of exact preflight matrix')
    parser.add_argument('--collector-public-key',type=pathlib.Path,help='Independently enrolled collector public key PEM (required with preflight checkpoint)')
    parser.add_argument('--strict-external-preflight',action='store_true',help='Require separate collector manifest, sealed shard index, bounded clock evidence and distinct reported kernels; NOT host authentication')
    p=parser.parse_args(argv)
    import os
    if p.strict_external_preflight:
        requirements=strict_external_preflight(p.launch_manifest,p.evidence_index,p.clock_evidence,
                                                p.require_distinct_kernels)
        if requirements:
            print('STRICT_EXTERNAL_PREFLIGHT_FAILED: '+'; '.join(requirements),file=sys.stderr)
            return 2
    if p.require_host_signatures and (p.host_pins is None or p.signed_host_report is None):
        print('HOST_SIGNATURE_PREFLIGHT_FAILED: pinned inventory and signed reports required',file=sys.stderr)
        return 2
    if p.require_host_session and (p.host_session is None or p.launch_manifest is None or
                                   p.host_pins is None or p.signed_host_report is None):
        print('COLLECTOR_SESSION_PREFLIGHT_FAILED: session, manifest, pins and signed reports required',file=sys.stderr)
        return 2
    try:r=merge(p.folders,p.verify_state,p.await_done_seconds,token=os.environ.get('DEROYQUE_SIM_READ_TOKEN'),server_metrics=p.server_metrics,require_distinct_kernels=p.require_distinct_kernels,gateway_witness=p.gateway_witness,launch_manifest=p.launch_manifest,evidence_index=p.evidence_index,clock_evidence=p.clock_evidence,max_clock_width_ms=p.max_clock_width_ms,host_pins=p.host_pins,signed_host_reports=p.signed_host_report,host_session=p.host_session,signed_clock_reports=p.signed_clock_report,readiness_matrix=p.readiness_matrix,readiness_reports=p.readiness_report,signed_readiness=p.signed_readiness,preflight_checkpoint=p.preflight_checkpoint,collector_public_key=p.collector_public_key)
    except (ValueError,KeyError,OSError,json.JSONDecodeError) as e:
        print(f'DISTRIBUTED_MERGE_FATAL: {e}',file=sys.stderr);return 2
    if p.output:
        if p.output.exists():print('Output file already exists',file=sys.stderr);return 2
        p.output.parent.mkdir(parents=True,exist_ok=True)
        p.output.write_text(json.dumps(r,indent=2,sort_keys=True)+'\n')
    print(json.dumps(r,indent=2,sort_keys=True))
    return 0 if r['outcome']=='PASS' else 1

if __name__=='__main__':sys.exit(main())
