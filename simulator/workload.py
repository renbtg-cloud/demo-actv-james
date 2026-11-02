"""Deterministic per-process ingress plans. No service or broker dependency."""
from __future__ import annotations
import math
import random

PROFILES = ('steady', 'ramp-up', 'burst', 'soak')
DISTRIBUTIONS = ('sequential', 'hotspot', 'skewed', 'unique')


def validate_workload(a):
    if a.profile not in PROFILES or a.distribution not in DISTRIBUTIONS:
        raise ValueError('Unknown workload profile or distribution')
    if a.keys > a.requests:
        raise ValueError('--keys must be <= --requests: every key needs an admission attempt')
    if a.distribution == 'unique' and a.keys != a.requests:
        raise ValueError('unique distribution requires --keys == --requests')
    if a.rate < 0 or not math.isfinite(a.rate):
        raise ValueError('--rate must be finite and >= 0')
    if a.duration_seconds < 0 or not math.isfinite(a.duration_seconds):
        raise ValueError('--duration-seconds must be finite and >= 0')
    if a.profile == 'ramp-up' and not (a.duration_seconds > 0 and 0 < a.ramp_start_rate <= a.rate):
        raise ValueError('ramp-up requires duration>0 and 0<ramp-start-rate<=rate')
    if a.profile == 'burst' and not (a.rate > 0 and a.burst_rate > a.rate
          and 0 < a.burst_width_seconds < a.burst_period_seconds):
        raise ValueError('burst requires rate>0, burst-rate>rate, 0<burst-width-seconds<burst-period-seconds')
    if a.profile == 'soak' and a.duration_seconds <= 0:
        raise ValueError('soak requires --duration-seconds>0')
    if a.profile == 'soak' and a.rate > 0 and abs(a.rate - a.requests/a.duration_seconds) / a.rate > 0.1:
        raise ValueError('soak --rate must match --requests/--duration-seconds within 10%')
    if not 0 < a.hotspot_percent <= 100:
        raise ValueError('--hotspot-percent must be >0 and <=100')
    if not 0 < a.hotspot_keys_percent <= 100:
        raise ValueError('--hotspot-keys-percent must be >0 and <=100')
    if not math.isfinite(a.skew_power) or a.skew_power < 1:
        raise ValueError('--skew-power must be finite and >=1')
    if a.requests > 5_000_000:
        raise ValueError('For safety, a plan may contain at most 5,000,000 POSTs')


def schedule_seconds(a):
    """Open-loop planned sends indexed by global sequence. Service backpressure may cause lateness.

    Burst uses periodic piecewise constant rates. Ramp-up uses inverse integrated linear rate.
    All calculations are deterministic, no naive Thread.sleep(request interval) accumulation.
    """
    count = a.requests
    if a.profile == 'soak':
        return [i * a.duration_seconds / (count - 1) for i in range(count)] if count > 1 else [0.0]
    if a.profile == 'steady':
        rate = a.rate if a.rate else (count / a.duration_seconds if a.duration_seconds else 0)
        return [i/rate for i in range(count)] if rate else [0.0]*count
    if a.profile == 'ramp-up':
        r0, r1, dur = a.ramp_start_rate, a.rate, a.duration_seconds
        slope = (r1-r0)/dur
        accrued = (r0+r1)*dur/2
        result=[]
        for i in range(count):
            if i >= accrued:
                t=dur+(i-accrued)/r1
            elif slope == 0:
                t=i/r0
            else:
                t=2*i/(r0+math.sqrt(r0*r0+2*slope*i))
            result.append(t)
        return result
    # Burst: integrate alternating peak and baseline over each period to find nth event time.
    base, peak = a.rate, a.burst_rate
    period, width = a.burst_period_seconds, a.burst_width_seconds
    capacity = peak*width+base*(period-width)
    result=[]
    for i in range(count):
        cycles = math.floor(i/capacity)
        remaining = i-cycles*capacity
        if remaining < peak*width:
            t=cycles*period+remaining/peak
        else:
            t=cycles*period+width+(remaining-peak*width)/base
        result.append(t)
    return result


def create_plan(a):
    """Return an iterator of (sequence, slot, variant, offset_ms) in global order.

    First wave covers all keys with variant 0, then collision distribution applies.
    """
    validate_workload(a)
    randomizer = random.Random(a.seed)
    hot_keys = max(1, math.ceil(a.keys*a.hotspot_keys_percent/100))
    for seq, offset in enumerate(schedule_seconds(a)):
        if seq < a.keys or a.distribution in ('unique', 'sequential'):
            slot = seq if seq < a.keys else seq % a.keys
        elif a.distribution == 'hotspot':
            slot = randomizer.randrange(hot_keys if randomizer.random()*100 < a.hotspot_percent else a.keys)
        else:
            slot = min(a.keys-1, int(a.keys * randomizer.random() ** a.skew_power))
        variant = int(seq >= a.keys and randomizer.random()*100 < a.conflict_percent)
        yield seq, slot, variant, round(offset*1000)
