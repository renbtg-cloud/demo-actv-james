#!/usr/bin/env python3
"""Cross-run comparison of actual stages, with explicit rejection/unknown categories.

Does not treat planned load as achieved load, or pinning counts as causal diagnoses.
"""
import argparse
import csv
import json
import pathlib

FIELDS=('run','tier','planned_jvms','planned_post_rate','planned_posts','observed_posts',
        'unexpected_http','observed_qps','p95_ms','schedule_p95_ms',
        'peak_active_jvms_http','service_cpu_cores_avg','producer_cpu_cores_avg',
        'cgroup_cpu_cores_avg','cgroup_nr_throttled_delta','cgroup_oom_kills_delta',
        'virtual_thread_pinned_events','virtual_thread_pinned_ms_total',
        'service_thread_dump_errors','verdict')

def combine(sources):
    rows=[]
    for source in sources:
        path=pathlib.Path(source)
        data=json.loads(path.read_text())
        if not isinstance(data.get('tiers'),list):raise ValueError('Missing tiers in '+str(path))
        old_sampling='lower bound' not in data.get('methodology',{}).get('sampling','')
        for item in data['tiers']:
            row={**{'run':path.parent.name},**{k:item.get(k) for k in FIELDS if k!='run'}}
            if old_sampling:
                # Earlier implementation subtracted a total that dropped to zero
                # after agent exit; zero was NOT a valid CPU measurement.
                row['producer_cpu_cores_avg']=None
            stage=path.parent/f"tier-{item['tier']:02d}-{item['planned_jvms']}jvm"
            log=stage/'run.log'
            if item.get('observed_posts',0)==0 and log.is_file() and 'Estimated JVM budget' in log.read_text():
                row['verdict']='RESOURCE_GUARD_REJECTED_STAGE_NOT_RUN'
            jfr=stage/'jfr-summary.json'
            if row['verdict']=='RESOURCE_GUARD_REJECTED_STAGE_NOT_RUN':
                row['producer_cpu_cores_avg']=None
            if jfr.is_file() and row['verdict']!='RESOURCE_GUARD_REJECTED_STAGE_NOT_RUN':
                recorded=json.loads(jfr.read_text()).get('events',{})
                row['virtual_thread_pinned_events']=recorded.get('counts',{}).get('jdk.VirtualThreadPinned',0)
                row['virtual_thread_pinned_ms_total']=recorded.get('virtual_thread_pinned_duration_total_ms')
            rows.append(row)
    return rows

def output(rows,prefix):
    prefix=pathlib.Path(prefix)
    prefix.parent.mkdir(parents=True,exist_ok=True)
    with pathlib.Path(str(prefix)+'.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=FIELDS)
        writer.writeheader();writer.writerows(rows)
    cols=('run','planned_jvms','planned_post_rate','observed_qps','p95_ms','schedule_p95_ms',
          'peak_active_jvms_http','producer_cpu_cores_avg','service_cpu_cores_avg',
          'cgroup_cpu_cores_avg','virtual_thread_pinned_events','verdict')
    lines=['# Deroyque saturation runs — comparison','',
           'Real HTTP admission attempts only; not provider completions. JVM count is observed only when `peak_active_jvms_http` is present.','',
           '| '+' | '.join(cols)+' |','|'+'|'.join('---' for _ in cols)+'|']
    for row in rows:lines.append('| '+' | '.join(str(row[k]) if row.get(k) is not None else 'n/a' for k in cols)+' |')
    lines+=['', 'Earlier runs collected producer CPU incorrectly after agents exited; their CPU field is shown as n/a, not zero. A memory-guard classification recovered from a stage log is explicitly an annotation, not a change to raw telemetry.','', 'The shared 4-CPU host also runs PostgreSQL, RabbitMQ and other processes. ',
            'A planned target is **not** an achieved throughput. CPU and JFR data are observational, not proof of a bottleneck. ',
            'JFR pinning counts depend on enabled event settings and thresholds, and a shallow stack cannot attribute the cause. ',
            'A resource-guard rejection means no workload was sent for that tier.','']
    pathlib.Path(str(prefix)+'.md').write_text('\n'.join(lines))
    return prefix

def main(argv=None):
    p=argparse.ArgumentParser()
    p.add_argument('reports',nargs='+',help='Paths to saturation.json files')
    p.add_argument('--output-prefix',required=True)
    a=p.parse_args(argv)
    rows=combine(a.reports);output(rows,a.output_prefix)
    print(f'COMPARISON_WRITTEN {len(rows)} stage rows, {a.output_prefix}.md/csv')
    return 0

if __name__=='__main__':raise SystemExit(main())
