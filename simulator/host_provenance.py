#!/usr/bin/env python3
"""Conservative producer-host provenance for reproducible multi-host evidence.

Self-reported metadata cannot prove physical independence; matching per-run kernel
fingerprints can, however, expose two purported hosts sharing one kernel.
"""
import hashlib
import os
from pathlib import Path
import platform
import re


def _read(path):
    try:
        return Path(path).read_text(encoding='ascii').strip()
    except (OSError, UnicodeError):
        return None


def _quota(value):
    if not value:
        return None
    parts = value.split()
    if len(parts) != 2 or parts[0] == 'max':
        return None
    try:
        amount, period = map(int, parts)
        return round(amount / period, 4) if amount > 0 and period > 0 else None
    except ValueError:
        return None


def snapshot(run_id):
    if not isinstance(run_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,48}', run_id):
        raise ValueError('invalid run identity')
    boot = _read('/proc/sys/kernel/random/boot_id')
    boot_hash = hashlib.sha256((run_id+'\0'+boot).encode('ascii')).hexdigest() if boot else None
    try:
        cpus = sorted(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        cpus = None
    return {'schema_version': 1, 'kernel_fingerprint': boot_hash,
        'kernel_release': platform.release(),
        'cpu_affinity_count': len(cpus) if cpus is not None else None,
        'cgroup_cpu_quota_cores': _quota(_read('/sys/fs/cgroup/cpu.max')),
        'cgroup_memory_limit': _read('/sys/fs/cgroup/memory.max'),
        'provenance_note': 'Self-reported by producer host; kernel fingerprint is SHA256(run_id + NUL + boot_id). Different values do not prove physical independence.'}


def evaluate_provenance(snapshots, run_id):
    if not snapshots or any(not isinstance(s, dict) or s.get('schema_version') != 1 for s in snapshots):
        status = 'UNKNOWN_MISSING_OR_LEGACY_HOST_EVIDENCE'
    else:
        keys = [s.get('kernel_fingerprint') for s in snapshots]
        valid = all(isinstance(k, str) and re.fullmatch('[0-9a-f]{64}', k) for k in keys)
        if not valid:
            status = 'UNKNOWN_KERNEL_FINGERPRINT_UNAVAILABLE'
        elif len(snapshots) < 2:
            status = 'INSUFFICIENT_SHARDS_FOR_KERNEL_COMPARISON'
        elif len(set(keys)) != len(keys):
            status = 'SAME_KERNEL_OBSERVED'
        else:
            status = 'DISTINCT_KERNEL_REPORTS_UNVERIFIED'
    return {'status': status, 'producer_shards': len(snapshots),
        'measurement_note': 'Matching salted kernel boot IDs disprove independent kernels. Different self-reported IDs do not prove separate hardware, administrative domains, clock synchronization, or trustworthy hosts.'}
