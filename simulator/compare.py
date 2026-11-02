#!/usr/bin/env python3
"""Compare two or more completed Deroyque input-simulator runs; no hidden benchmarks."""
import argparse
import csv
import json
import pathlib

FIELDS = [('label','Run'),('status','Status'),('profile','Profile'),('jvms','JVMs'),
          ('posts','POSTs'),('unexpected','Unexpected HTTP'),('violations','Invariant violations'),
          ('peak_jvms','Peak active JVMs'),('peak_http','Peak HTTP'),('qps','POST/s'),
          ('p95_ms','p95 ms'),('p99_ms','p99 ms'),('lag_p95_ms','Schedule lag p95 ms')]

def load_report(p):
    doc=json.loads(p.read_text())
    plan=doc['plan']; observed=doc['observed']
    return dict(label=p.parent.name, status=doc['outcome'],profile=plan.get('profile','legacy'),
        jvms=plan['jvms'],posts=observed['records'],
        unexpected=observed['unexpected_http_or_transport'],
        violations=len(doc['violations'])+len(doc['harness_errors'])+len(doc['state_verification_errors']),
        peak_jvms=observed['peak_jvms_with_inflight_http'],peak_http=observed['peak_inflight_http'],
        qps=observed['post_attempts_per_second'],p95_ms=observed['latency_ms']['95'],
        p99_ms=observed['latency_ms']['99'],
        lag_p95_ms=observed.get('schedule_lag_ms',{}).get('95','n/a'))

def run(argv=None):
    p=argparse.ArgumentParser(description='Compare actual simulator summary.json files')
    p.add_argument('reports',type=pathlib.Path,nargs='+')
    p.add_argument('--output-prefix',type=pathlib.Path,required=True)
    a=p.parse_args(argv)
    if len(a.reports)<2:p.error('Provide at least two actual summary.json files')
    rows=[load_report(r) for r in a.reports]
    a.output_prefix.parent.mkdir(parents=True,exist_ok=True)
    csv_path=pathlib.Path(str(a.output_prefix)+'.csv')
    with csv_path.open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=[x[0] for x in FIELDS]);writer.writeheader();writer.writerows(rows)
    md_path=pathlib.Path(str(a.output_prefix)+'.md')
    lines=['# Deroyque ingress workload comparison','',
      'Figures are **observed on the client**, not controlled server-only benchmarks. '
      'Different profiles, workloads or host contention make comparisons non-causal.','',
      '| '+' | '.join(label for _,label in FIELDS)+' |',
      '| '+' | '.join('---' for _ in FIELDS)+' |']
    for row in rows:lines.append('| '+' | '.join(str(row[key]) for key,_ in FIELDS)+' |')
    lines+=['','**Scope:** POST admission only. Provider completion and exactly-once effects are not measured.','']
    md_path.write_text('\n'.join(lines),encoding='utf-8')
    print('COMPARISON_WRITTEN',md_path,csv_path)
    return 0

if __name__=='__main__':raise SystemExit(run())
