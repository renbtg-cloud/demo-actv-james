#!/usr/bin/env python3
"""Fail-closed evidence gate for real Spring integration result JSON.

A green process exit is not evidence that any intended assertions ran.
This tool checks a scoped, sanitized result file and prints only counters.
"""
import argparse
import json
from pathlib import Path
import sys

# Baselines from the completed real Milestone 27 matrix (run 41496282564).
SCENARIOS={
    'staggered-crashes':('spring-real-result.json',44),
    'exact-fault-windows':('spring-fault-windows.json',44),
    'competing-windows':('spring-chaos-result.json',44),
    'dependency-interruptions':('spring-chaos-result.json',19),
    'late-and-contradiction':('spring-chaos-result.json',16),
    'same-row-contention':('spring-same-row-race.json',22),
    'gateway-witness':('spring-gateway-witness.json',20),
    'gateway-soak':('spring-gateway-soak.json',20),
    'gateway-trials':('spring-gateway-trials.json',15),
    'jwt-security':('spring-jwt-security.json',69),
    'oidc-keycloak':('spring-keycloak-oidc.json',26),
    'local-keycloak':('spring-local-keycloak.json',26),
}


def no_duplicate_json_pairs(items):
    out={}
    for key,value in items:
        if key in out:raise ValueError('duplicate JSON field in evidence')
        out[key]=value
    return out


def validate(scenario,result):
    if scenario not in SCENARIOS:raise ValueError('unsupported CI scenario')
    if not isinstance(result,dict):raise ValueError('evidence is not a JSON object')
    _,minimum=SCENARIOS[scenario]
    check_list=result.get('tests') if scenario=='staggered-crashes' else result.get('checks')
    if not isinstance(check_list,list):raise ValueError('missing assertion list')
    total=result.get('total');passed=result.get('passed');failed=result.get('failed')
    if any(type(x) is not int for x in (total,passed,failed)):
        raise ValueError('non-integer assertion counts')
    if total<minimum or total!=len(check_list) or passed!=total or failed!=0:
        raise ValueError('empty, incomplete or inconsistent assertion counts')
    if any(not isinstance(c,dict) or c.get('passed') is not True or
           not isinstance(c.get('name'),str) or not c['name'] for c in check_list):
        raise ValueError('missing or unsuccessful assertion details')
    exit_value=result.get('exit' if scenario in
                          ('competing-windows','dependency-interruptions','late-and-contradiction')
                          else 'exit_code')
    if type(exit_value) is not int or exit_value!=0:
        raise ValueError('integration runner returned nonzero or missing exit code')
    if scenario=='staggered-crashes':
        if (result.get('mode')!='boot' or result.get('flyway_runtime_verified') is not True or
            result.get('engine_completed') is not True or not isinstance(result.get('adversarial'),dict)):
            raise ValueError('actual Spring/Flyway/worker survival proof absent')
    elif scenario=='jwt-security':
        if result.get('signed_effect_verified') is not True:
            raise ValueError('signed independent provider effect not verified')
    elif scenario in ('oidc-keycloak','local-keycloak'):
        if (result.get('issuer_verified') is not True or result.get('signed_provider_effect_verified') is not True):
            raise ValueError('genuine issuer or signed independent effect not verified')
    elif scenario in ('gateway-witness','gateway-soak'):
        m=result.get('measurement')
        soak=scenario=='gateway-soak'
        expected_posts=240 if soak else 80
        expected_effects=60 if soak else 20
        if (result.get('completed') is not True or
                result.get('profile')!=('soak' if soak else 'short') or
                result.get('workload_duration_seconds')!=(120 if soak else 0) or
                not isinstance(m,dict) or m.get('global_recorded_posts')!=expected_posts or
                type(m.get('global_throughput_qps')) not in (int,float) or
                m['global_throughput_qps']<=0 or
                not isinstance(m.get('gateway_witness'),dict) or
                m['gateway_witness'].get('total_http_responses')!=expected_posts or
                m['gateway_witness'].get('backend_count')!=2 or
                m.get('verified_states',{}).get('DONE')!=expected_effects or
                result.get('business_effects')!=expected_effects or
                result.get('durable_requests')!=expected_effects or
                result.get('signed_attestations')!=expected_effects or
                result.get('identity_graph_verified') is not True or
                result.get('provider_per_key_verified') is not True or
                result.get('independent_ed25519_verified') is not True):
            raise ValueError('real gateway/Spring/provider per-identity end-to-end proof absent')
    elif scenario=='gateway-trials':
        c=result.get('comparison')
        if (result.get('completed') is not True or not isinstance(c,dict) or
                c.get('trial_count')!=3 or c.get('backend_count')!=2 or
                c.get('host_provenance_status')!='SAME_KERNEL_OBSERVED' or
                type(c.get('median_gateway_post_qps')) not in (int,float) or
                c['median_gateway_post_qps']<=0 or
                type(c.get('median_absolute_deviation')) not in (int,float) or
                c['median_absolute_deviation']<0 or
                not isinstance(c.get('workload_sha256'),str) or
                len(c['workload_sha256'])!=64):
            raise ValueError('genuine repeated real Spring trials absent')
    elif scenario=='same-row-contention':
        if result.get('completed') is not True or result.get('business_effects')!=1:
            raise ValueError('same-row concurrent work was not proven settled once')
    else:
        if not isinstance(result.get('cases'),list) or not result['cases']:
            raise ValueError('fault-injection cases absent')
    return {'scenario':scenario,'failed':failed,'passed':passed,'total':total}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--scenario',choices=sorted(SCENARIOS),required=True)
    p.add_argument('--result-dir',type=Path,required=True)
    p.add_argument('--summary-path',type=Path)
    a=p.parse_args()
    try:
        result_path=a.result_dir/SCENARIOS[a.scenario][0]
        doc=json.loads(result_path.read_text(),object_pairs_hook=no_duplicate_json_pairs)
        audit=validate(a.scenario,doc)
    except (OSError,ValueError,TypeError,KeyError,OverflowError) as error:
        print('CI_EVIDENCE_REJECTED '+a.scenario+' reason='+type(error).__name__,file=sys.stderr)
        return 1
    line=f'CI_EVIDENCE_GATE FAILURES {audit["failed"]}/{audit["total"]} PASSED {audit["passed"]}/{audit["total"]} SCENARIO {a.scenario}'
    print(line,flush=True)
    if a.summary_path:
        # Only the controlled scenario identifier and integer counts are included.
        with a.summary_path.open('a',encoding='utf-8') as s:
            s.write(f'### Deroyque real integration: {a.scenario}\n\n')
            s.write(f'- Failures: **0/{audit["total"]}**\n- Passed: **{audit["total"]}/{audit["total"]}**\n')
            s.write('- Evidence: actual assertion list, zero exit, scenario-specific proof gates verified\n')
    return 0

if __name__=='__main__':sys.exit(main())
