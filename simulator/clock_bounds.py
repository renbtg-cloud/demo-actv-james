#!/usr/bin/env python3
"""Conservative UTC offset intervals from collector-held four-timestamp probes.

For request A->B, (t0,t3) are A timestamps and (t1,t2) B timestamps.
Assuming nonnegative network transit and stable clock rates over the probe,
remote-minus-local offset lies in [t1-t3, t2-t0]. This is NEVER proof of
synchronized physical hosts, a trusted NTP server or authenticated timestamps.
"""
import argparse
import json
import pathlib
import sys


def audit_clock_probes(document, run_id, shards, max_width_ms=100):
    errors = []
    if not isinstance(document, dict) or document.get('schema_version') != 1:
        return None, ['Clock evidence schema v1 required']
    if document.get('run_id') != run_id:
        return None, ['Clock evidence run ID mismatch']
    if type(shards) is not int or shards < 2 or type(max_width_ms) not in (int, float) or not 0 < max_width_ms <= 10000:
        return None, ['Invalid clock evidence requirements']
    entries = document.get('shards')
    if not isinstance(entries, list) or len(entries) != shards:
        return None, ['Clock evidence missing/extra shards']
    result = []
    for i, entry in enumerate(entries):
        try:
            if not isinstance(entry, dict) or entry.get('shard_index') != i or type(entry.get('shard_index')) is not int:
                raise ValueError('Clock shard index/order mismatch')
            samples = entry.get('samples')
            if not isinstance(samples, list) or not 3 <= len(samples) <= 64:
                raise ValueError('Expected 3..64 four-timestamp probes per shard')
            lowers, uppers = [], []
            for s in samples:
                keys = ('local_send_ns', 'remote_recv_ns', 'remote_send_ns', 'local_recv_ns', 'local_elapsed_monotonic_ns')
                if not isinstance(s, dict) or any(type(s.get(k)) is not int for k in keys):
                    raise ValueError('Missing or invalid integer timestamp')
                a,b,c,d,mono = (s[k] for k in keys)
                if min(a,b,c,d) < 0 or d <= a or c < b or mono <= 0 or d-a > 15_000_000_000:
                    raise ValueError('Invalid clock/round-trip chronology')
                # Refuse UTC jumps that could invalidate the conservative bounds.
                if abs(d-a-mono) > max(2_000_000, (d-a)//20):
                    raise ValueError('Local wall-clock jump or unstable clock detected')
                lo,hi = b-d,c-a
                if lo > hi:
                    raise ValueError('No feasible nonnegative network transfer')
                lowers.append(lo)
                uppers.append(hi)
            lo, hi = max(lowers), min(uppers)
            if hi < lo:
                raise ValueError('Inconsistent offset intervals across probes')
            width = hi-lo
            if width > max_width_ms * 1_000_000:
                raise ValueError('Clock offset uncertainty exceeds configured maximum')
            result.append({'shard': i, 'offset_lower_ns': lo, 'offset_upper_ns': hi,
                           'uncertainty_width_ns': width, 'probe_count': len(samples)})
        except (ValueError,KeyError,TypeError) as exc:
            errors.append(f'shard {i}: {exc}')
    if errors:
        return None,errors
    return {'status':'BOUNDED_SELF_REPORTED_NOT_AUTHENTICATED',
            'reference_id':document.get('reference_id'), 'per_shard':result,
            'cross_host_wall_time_qps':None,
            'measurement_note':'Offset bounds assume stable clocks and nonnegative one-way transit; they do not authenticate remote endpoints, prove physical isolation, or justify cross-host peak concurrency.'}, []


def main(argv=None):
    p=argparse.ArgumentParser(description='Audit four-timestamp bounded clock evidence without claiming trust')
    p.add_argument('--file',type=pathlib.Path,required=True)
    p.add_argument('--run-id',required=True)
    p.add_argument('--shards',type=int,required=True)
    p.add_argument('--max-width-ms',type=float,default=100)
    a=p.parse_args(argv)
    try:
        if a.file.stat().st_size > 2 * 1024 * 1024:
            raise ValueError('Oversized clock evidence')
        doc=json.loads(a.file.read_text(encoding='utf-8'))
        report, errors=audit_clock_probes(doc,a.run_id,a.shards,a.max_width_ms)
        print(json.dumps({'report':report,'errors':errors},indent=2,sort_keys=True))
        return 1 if errors else 0
    except (OSError,ValueError,json.JSONDecodeError) as exc:
        print('CLOCK_AUDIT_FATAL',exc,file=sys.stderr)
        return 2


if __name__=='__main__':sys.exit(main())
