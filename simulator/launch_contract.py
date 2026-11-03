"""Replay and independently check the *intended* launch manifest and scripts.

A checksum binds an arbitrary script to an arbitrary manifest; it does not
prove the script actually performs the work claimed by that manifest. Replay
only the known v2 generator vocabulary, reject unknown fields, and compare
canonical bytes. This is a local consistency control, NOT independent host
identity, trustworthy manifest provenance, or a secure sandbox for scripts.
"""
import argparse
import json
import itertools
import re

_MANIFEST_KEYS = frozenset({
    'schema_version','launch_plan','run_id','target_url','shards',
    'jvms_per_host','global_posts','shared_keys','start_at_epoch_ms',
    'start_at_utc','clock_requirement','cross_host_throughput',
    'launch_sha256',
})
_PLAN_KEYS = frozenset({
    'profile','distribution','duration_seconds','seed',
    'global_attempted_posts','shared_keys','conflict_percent',
    'target_qps','shard_count','start_at_ms','ramp_start_rate',
    'burst_rate','burst_width_seconds','burst_period_seconds',
    'hotspot_percent','hotspot_keys_percent','skew_power',
    'heap_max_mb','concurrency_per_jvm',
})
_SHA256 = re.compile(r'[0-9a-f]{64}\Z')


def replay_manifest(manifest):
    """Return canonical launch script bytes for every host or fail closed.

    Reuse the generator with a synthetic 30s delay solely to regenerate its
    original start instant; nothing here executes launch scripts or connects
    to a network. The resulting v2 manifest must be byte-for-byte equivalent
    under stable JSON serialization, including field *types* and all hashes.
    """
    if not isinstance(manifest,dict) or set(manifest)!=_MANIFEST_KEYS:
        raise ValueError('Unknown or incomplete launch manifest schema')
    if type(manifest['schema_version']) is not int or manifest['schema_version']!=2:
        raise ValueError('Unsupported launch manifest version')
    plan=manifest['launch_plan']
    if not isinstance(plan,dict) or set(plan)!=_PLAN_KEYS:
        raise ValueError('Unknown or incomplete launch plan schema')
    n=manifest['shards']; counts=manifest['jvms_per_host']
    start=manifest['start_at_epoch_ms']
    if (type(n) is not int or not 2<=n<=256 or
        type(counts) is not list or len(counts)!=n or
        any(type(v) is not int or not 1<=v<=256 for v in counts) or
        type(start) is not int or not 30000 < start < 253402200000000):
        raise ValueError('Invalid canonical shard, JVM or barrier specification')
    index=manifest['launch_sha256']
    expected_names={f'launch-host-{i:03d}.sh' for i in range(n)}
    if (not isinstance(index,dict) or set(index)!=expected_names or
        any(not isinstance(v,str) or not _SHA256.fullmatch(v) for v in index.values())):
        raise ValueError('Incomplete or unsafe canonical launch checksum inventory')
    # Reject hostile/out-of-range fields before invoking the generator. The
    # generator applies workload and endpoint constraints a second time.
    for name in ('global_posts','shared_keys'):
        if type(manifest[name]) is not int or not 1<=manifest[name]<=5_000_000:
            raise ValueError('Invalid canonical workload volume')
    if type(manifest['run_id']) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{1,48}',manifest['run_id']):
        raise ValueError('Invalid canonical run ID')
    args=argparse.Namespace(
        url=manifest['target_url'],shards=n,
        jvms_per_host=','.join(map(str,counts)),
        requests=manifest['global_posts'],keys=manifest['shared_keys'],
        run_id=manifest['run_id'],rate=plan['target_qps'],
        profile=plan['profile'],distribution=plan['distribution'],
        conflict_percent=plan['conflict_percent'],seed=plan['seed'],
        concurrency=plan['concurrency_per_jvm'],
        heap_max_mb=plan['heap_max_mb'],duration_seconds=plan['duration_seconds'],
        ramp_start_rate=plan['ramp_start_rate'],burst_rate=plan['burst_rate'],
        burst_width_seconds=plan['burst_width_seconds'],
        burst_period_seconds=plan['burst_period_seconds'],
        hotspot_percent=plan['hotspot_percent'],
        hotspot_keys_percent=plan['hotspot_keys_percent'],
        skew_power=plan['skew_power'],start_after_seconds=30,
    )
    # Reject unsafe endpoint strings using the same diagnostic vocabulary as
    # host preflight before replaying the generator, so a malformed URL cannot
    # be hidden behind a generic canonical-manifest error.
    from host_readiness import _parse_preflight_target
    _parse_preflight_target(manifest['target_url'])
    # Import at invocation time because the generator imports the host
    # endpoint policy; this must not create an import-time dependency cycle.
    from distributed_prepare import build_scripts
    stable=lambda value:json.dumps(value,sort_keys=True,separators=(',', ':'),
                                   ensure_ascii=True,allow_nan=False)
    # Version 2 stored numeric values as normalized floats even when the
    # originating CLI left integer-typed defaults untouched. The shell text
    # nevertheless used str(original), i.e. '60' vs '60.0'. Reproduce BOTH
    # legitimate old forms, and match the *entire* declared hash inventory;
    # do not silently rewrite already-issued historical evidence.
    active=['rate']
    if plan['duration_seconds']>0:active.append('duration_seconds')
    if plan['profile']=='ramp-up':active.append('ramp_start_rate')
    if plan['profile']=='burst':active.extend(('burst_rate','burst_width_seconds','burst_period_seconds'))
    if plan['distribution']=='hotspot':active.extend(('hotspot_percent','hotspot_keys_percent'))
    if plan['distribution']=='skewed':active.append('skew_power')
    integer_options=[key for key in active
                     if type(getattr(args,key)) is float and getattr(args,key).is_integer()]
    try:
        # The number of optional formats is bounded by the fixed vocabulary:
        # at most 7 binary flags for any profile/distribution combination.
        for choices in itertools.product((False,True),repeat=len(integer_options)):
            candidate=argparse.Namespace(**vars(args))
            for key,as_integer in zip(integer_options,choices):
                if as_integer:setattr(candidate,key,int(getattr(candidate,key)))
            scripts,regenerated=build_scripts(candidate,now_ms=start-30000)
            if stable(manifest)==stable(regenerated):
                return {name:body.encode('utf-8') for name,body in scripts.items()}
    except (KeyError,TypeError,ValueError,OverflowError,ArithmeticError) as exc:
        raise ValueError(f'Invalid or contradictory canonical launch plan: {exc}') from exc
    raise ValueError('Manifest differs from every valid canonical launch encoding')
