#!/usr/bin/env python3
"""Repeat the genuine isolated Spring gateway workload three times on one CI runner.

Runs each real two-Spring-JVM/PostgreSQL/RabbitMQ/SQLite experiment sequentially,
then applies the strict same-plan witness comparator. This is descriptive
repeatability evidence, not performance certification or physical host diversity.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'simulator'))
from trial_compare import compare

ROOT=Path(__file__).resolve().parents[1]
P=argparse.ArgumentParser(description=__doc__)
P.add_argument('--boot-jar',type=Path,required=True)
P.add_argument('--output',type=Path,required=True)
ARGS=P.parse_args()
RESULT={'scenario':'real-gateway-three-trials','checks':[],'completed':False}

def check(ok,name):
    ok=bool(ok);RESULT['checks'].append({'name':name,'passed':ok});n=len(RESULT['checks'])
    print(f'{"PASS" if ok else "FAIL"} {n}/{n} {name}',flush=True)
    if not ok:raise AssertionError(name)

def run():
    check(ARGS.boot_jar.is_file(),'real Spring executable JAR available')
    with tempfile.TemporaryDirectory(prefix='deroyque-real-trials-') as temp:
        base=Path(temp);samples=[];identities=[]
        for index in range(3):
            summary=base/f'trial-{index}.json'
            merged=base/f'trial-{index}-merged.json'
            cmd=[sys.executable,str(ROOT/'tests/run_spring_gateway_witness.py'),
                 '--boot-jar',str(ARGS.boot_jar),'--output',str(summary),
                 '--merged-output',str(merged),'--profile','short']
            p=subprocess.run(cmd,text=True,capture_output=True,timeout=360)
            if p.returncode:
                print('GATEWAY_TRIAL_FAILURE index='+str(index)+' exit='+str(p.returncode),flush=True)
            check(p.returncode==0 and summary.is_file() and merged.is_file(),
                  f'isolated real Spring trial {index+1}/3 executed successfully')
            one=json.loads(summary.read_text())
            check(one.get('completed') is True and one.get('business_effects')==20 and
                  one.get('durable_requests')==20 and one.get('signed_attestations')==20 and
                  one.get('identity_graph_verified') is True and one.get('provider_per_key_verified') is True and
                  one.get('independent_ed25519_verified') is True and
                  one.get('terminal_signed_lineage_verified') is True and
                  one.get('uncontaminated_schema_verified') is True and
                  one.get('provider_index_and_time_verified') is True and
                  one.get('payload_contract_verified') is True,
                  f'trial {index+1}/3 independently reconciled signed and remote effects')
            sample=json.loads(merged.read_text())
            samples.append(sample);identities.append(sample.get('run_id'))
        check(len(set(identities))==3,'every experiment has an independent unique run ID')
        comparison=compare(samples)
        check(comparison['outcome']=='DESCRIPTIVE_ONLY','three complete trials accepted by strict comparator')
        check(comparison['trial_count']==3,'exactly three independently executed observations')
        check(comparison['backend_count']==2,'two real Spring backend listeners in every trial')
        check(comparison['host_provenance_status']=='SAME_KERNEL_OBSERVED',
              'the experiment does not fabricate physically independent hosts')
        metric=comparison['gateway_completed_post_qps']
        check(0<metric['min']<=metric['median']<=metric['max'],
              'finite positive median, minimum and maximum rates measured')
        check(metric['median_absolute_deviation']>=0,'nonnegative dispersion represented explicitly')
        check(len({r['run_id'] for r in comparison['trials']})==3,
              'three unique trial identities remain in aggregate evidence')
        RESULT['comparison']={
            'trial_count':3,'median_gateway_post_qps':metric['median'],
            'median_absolute_deviation':metric['median_absolute_deviation'],
            'min_gateway_post_qps':metric['min'],'max_gateway_post_qps':metric['max'],
            'backend_count':comparison['backend_count'],
            'host_provenance_status':comparison['host_provenance_status'],
            'workload_sha256':comparison['workload_sha256']}
        RESULT['completed']=True

def main():
    code=1
    try:run();code=0
    except Exception as e:
        RESULT['error_type']=type(e).__name__
        print('GATEWAY_TRIALS_ERROR '+type(e).__name__,flush=True)
    finally:
        failed=sum(not c['passed'] for c in RESULT['checks']);total=len(RESULT['checks'])
        RESULT.update({'failed':failed,'passed':total-failed,'total':total,'exit_code':code})
        print(f'GATEWAY_TRIALS_RESULT FAILURES {failed}/{total} PASSED {total-failed}/{total}',flush=True)
        ARGS.output.parent.mkdir(parents=True,exist_ok=True)
        ARGS.output.write_text(json.dumps(RESULT,indent=2,sort_keys=True)+'\n')
    return code

if __name__=='__main__':raise SystemExit(main())
