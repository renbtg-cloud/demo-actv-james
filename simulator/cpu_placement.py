"""CPU affinity validation and disclosure for shared-host experiments.

This is CPU placement, NOT physical isolation or a dedicated CPU reservation.
"""
import os
import pathlib


def parse_cpus(text, available=None):
    if text is None:
        return None
    if not isinstance(text, str) or not text.strip():
        raise ValueError('CPU list must not be empty')
    cpus=set()
    for part in text.split(','):
        pair=part.strip().split('-')
        if len(pair)>2 or not all(x.isdecimal() for x in pair):
            raise ValueError(f'Invalid CPU entry: {part!r}')
        start=int(pair[0]); end=int(pair[-1])
        if start>end or end-start>4096:
            raise ValueError('Invalid CPU range')
        cpus.update(range(start,end+1))
    if not cpus:
        raise ValueError('Empty CPU list')
    if available is None:
        available=os.sched_getaffinity(0)
    if not cpus.issubset(set(available)):
        raise ValueError(f'CPU selection {sorted(cpus)} outside available {sorted(available)}')
    return cpus


def format_cpus(cpus):
    return ','.join(str(i) for i in sorted(cpus)) if cpus else None


def pid_cpus(pid):
    try:
        return sorted(os.sched_getaffinity(int(pid)))
    except (OSError, ValueError):
        return None


def proc_cpu_threads(pid):
    """Count actual OS threads with CPU affinity distinct from the leader."""
    parent=pid_cpus(pid)
    if parent is None:
        return None
    other=0; total=0
    try:
        for tid in (pathlib.Path('/proc')/str(pid)/'task').iterdir():
            allowed=pid_cpus(int(tid.name))
            if allowed is None:continue
            total+=1
            if allowed!=parent:other+=1
    except OSError:return None
    return {'leader_cpus':parent,'thread_count_checked':total,'threads_with_other_affinity':other}
