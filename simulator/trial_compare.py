#!/usr/bin/env python3
"""Conservative comparisons of multiple completed witness-gateway trials.

Each input must already have passed the global distributed witness audit and
terminal-state verification. This is descriptive, never proof of causality,
service-only saturation, or identical runtime/hardware conditions.
"""
import argparse
import json
import math
from pathlib import Path
import statistics
import sys


def compare(sources):
    if not 3 <= len(sources) <= 100:
        raise ValueError('Provide 3..100 independently completed trials')
    records = []
    seen = set()
    signature = None
    host_status = None
    backend_count = None
    for index, source in enumerate(sources):
        data = json.loads(Path(source).read_text(encoding='utf-8')) if isinstance(source,(str,Path)) else source
        if not isinstance(data,dict) or data.get('outcome')!='PASS':
            raise ValueError(f'Trial {index}: fail-closed global PASS evidence missing')
        run_id = data.get('run_id')
        if not isinstance(run_id,str) or not run_id or run_id in seen:
            raise ValueError(f'Trial {index}: missing or repeated run identity')
        seen.add(run_id)
        sample_sig=data.get('workload_sha256')
        if not isinstance(sample_sig,str) or len(sample_sig)!=64 or any(c not in '0123456789abcdef' for c in sample_sig):
            raise ValueError(f'Trial {index}: missing stable workload signature')
        if signature is None:signature=sample_sig
        elif signature!=sample_sig:raise ValueError('Incompatible workload parameters across trials')
        gateway=data.get('gateway_witness')
        if (not isinstance(gateway,dict) or gateway.get('scope')!='one_gateway_clock_across_backends' or
                type(gateway.get('backend_count')) is not int or gateway['backend_count']<1 or
                type(gateway.get('total_http_responses')) is not int):
            raise ValueError(f'Trial {index}: verified gateway witness absent')
        rate=gateway.get('gateway_completed_post_qps')
        if type(rate) not in (int,float) or not math.isfinite(rate) or rate<=0:
            raise ValueError(f'Trial {index}: invalid observed completion rate')
        keys=data.get('global_keys');n=data.get('global_requested_posts');got=data.get('global_recorded_posts')
        states=data.get('verified_states')
        if (type(keys) is not int or keys<=0 or type(n) is not int or n<keys or
                type(got) is not int or got!=n or gateway['total_http_responses']!=n or
                not isinstance(states,dict) or states.get('DONE')!=keys or type(states.get('DONE')) is not int or
                data.get('harness_errors') or data.get('global_invariant_violations') or
                data.get('state_verification_errors') or data.get('unexpected_http_or_transport')):
            raise ValueError(f'Trial {index}: incomplete request, state or side-effect evidence')
        if backend_count is None:backend_count=gateway['backend_count']
        elif backend_count!=gateway['backend_count']:
            raise ValueError('Backend count changed across trials')
        evidence=data.get('host_evidence')
        status=evidence.get('status') if isinstance(evidence,dict) else None
        if not isinstance(status,str):raise ValueError('Missing host-provenance classification')
        if host_status is None:host_status=status
        elif host_status!=status:raise ValueError('Host-provenance classification differs across trials')
        records.append({'run_id':run_id,'gateway_post_qps':rate,
                        'gateway_window_seconds':gateway.get('gateway_window_seconds')})
    values=[x['gateway_post_qps'] for x in records]
    median=statistics.median(values)
    spread=statistics.median(abs(x-median) for x in values)
    return {'outcome':'DESCRIPTIVE_ONLY','trial_count':len(records),'workload_sha256':signature,
            'backend_count':backend_count,'host_provenance_status':host_status,
            'gateway_completed_post_qps':{'min':min(values),'median':median,
                'max':max(values),'median_absolute_deviation':round(spread,3)},
            'trials':records,
            'measurement_note':'Gateway upstream HTTP response rates on one clock per trial; no confidence bounds, causal attribution, controlled CPU isolation, provider-effect throughput, or production benchmark certification. Operator must separately document environment and tunnel configurations.'}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reports',nargs='+',type=Path)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args(argv)
    try:
        result=compare(args.reports)
        if args.output:
            if args.output.exists():raise ValueError('Refusing to overwrite comparison evidence')
            args.output.parent.mkdir(parents=True,exist_ok=True)
            with args.output.open('x',encoding='utf-8') as f:
                f.write(json.dumps(result,indent=2,sort_keys=True)+'\n')
        print(json.dumps(result,indent=2,sort_keys=True))
        return 0
    except (ValueError,TypeError,OSError,json.JSONDecodeError) as e:
        print('WITNESS_TRIALS_REJECTED '+type(e).__name__,file=sys.stderr)
        return 2


if __name__=='__main__':sys.exit(main())
