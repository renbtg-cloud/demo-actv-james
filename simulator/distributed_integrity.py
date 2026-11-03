#!/usr/bin/env python3
"""Offline launch and shard-evidence checks; SHA256 is not host authentication.

The independent collector must retain launch manifest and evidence index in a
trusted location. A forged index paired with forged data will pass hashing.
"""
import argparse
import hashlib
import json
import pathlib
import re
import sys

_MAX_MEMBER = 128 * 1024 * 1024
_MAX_TOTAL = 512 * 1024 * 1024
_HEX = re.compile(r'[0-9a-f]{64}\Z')


def _json(value):
    if isinstance(value, (str, pathlib.Path)):
        p = pathlib.Path(value)
        if p.stat().st_size > 2 * 1024 * 1024:
            raise ValueError('Evidence index/manifest too large')
        return json.loads(p.read_text(encoding='utf-8'))
    return value


def verify_launch(manifest, shard_summaries):
    """Bound every shard to the exact deterministic *intended* launch plan."""
    m = _json(manifest)
    errors = []
    if not isinstance(m, dict) or m.get('schema_version') != 2:
        return ['Launch manifest v2 required']
    if not isinstance(m.get('launch_plan'), dict):
        return ['Launch manifest lacks explicit workload plan']
    plan = m['launch_plan']
    try:
        # Reject self-consistent but forged/contradictory v2 manifests, not
        # just differences between summaries and the supplied manifest.
        from launch_contract import replay_manifest
        replay_manifest(m)
        count = m['shards']
        jvms = m['jvms_per_host']
        if type(count) is not int or not (2 <= count <= 256) or type(jvms) is not list or len(jvms) != count:
            raise ValueError('invalid shard count/JVM layout')
        if not all(type(v) is int and 1 <= v <= 256 for v in jvms):
            raise ValueError('invalid JVM counts')
        if len(shard_summaries) != count:
            raise ValueError('missing or extra shard summaries')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,48}', m['run_id']):
            raise ValueError('invalid run ID')
        if type(m['start_at_epoch_ms']) is not int or m['start_at_epoch_ms'] <= 0:
            raise ValueError('invalid launch barrier')
        # Require every plan field to be present and equal, including optional
        # hotspot/burst parameters that earlier aggregators might overlook.
        required = ('profile', 'distribution', 'duration_seconds', 'seed',
                    'global_attempted_posts', 'shared_keys', 'conflict_percent',
                    'target_qps', 'shard_count', 'start_at_ms', 'ramp_start_rate',
                    'burst_rate', 'burst_width_seconds', 'burst_period_seconds',
                    'hotspot_percent', 'hotspot_keys_percent', 'skew_power',
                    'heap_max_mb', 'concurrency_per_jvm')
        if set(plan) != set(required):
            raise ValueError('incomplete or unexpected launch plan fields')
        for i, s in enumerate(shard_summaries):
            p = s.get('plan') if isinstance(s, dict) else None
            if not isinstance(p, dict):
                raise ValueError('missing shard plan')
            if s.get('run_id') != m['run_id'] or s.get('target') != m['target_url']:
                raise ValueError(f'shard {i}: run ID/target differs from launch')
            if type(p.get('shard_index')) is not int or p['shard_index'] != i:
                raise ValueError(f'shard {i}: wrong shard index/order')
            if type(p.get('jvms')) is not int or p['jvms'] != jvms[i]:
                raise ValueError(f'shard {i}: wrong JVM count')
            for field in required:
                if type(p.get(field)) is not type(plan[field]) or p[field] != plan[field]:
                    raise ValueError(f'shard {i}: launch mismatch in {field}')
        if plan['shard_count'] != count or plan['start_at_ms'] != m['start_at_epoch_ms']:
            raise ValueError('manifest contradictory barrier/shard count')
        if plan['global_attempted_posts'] != m['global_posts'] or plan['shared_keys'] != m['shared_keys']:
            raise ValueError('manifest contradictory post/key totals')
    except (KeyError, TypeError, ValueError) as exc:
        errors.append(str(exc))
    return errors


def verify_launch_scripts(folder):
    """Check generated shell scripts were not accidentally changed after planning."""
    d = pathlib.Path(folder)
    manifest = _json(d / 'manifest.json')
    if not isinstance(manifest, dict) or manifest.get('schema_version') != 2:
        return ['Launch manifest v2 required']
    try:
        from launch_contract import replay_manifest
        from host_readiness import _read_launcher_snapshot
        canonical = replay_manifest(manifest)
    except (KeyError, ValueError, TypeError, OverflowError) as exc:
        return [f'Invalid canonical launch plan: {exc}']
    errors = []
    for name, expected_bytes in canonical.items():
        p = d / name
        try:
            actual = _read_launcher_snapshot(p)
        except (OSError, ValueError) as exc:
            errors.append(f'Unsafe or missing launcher: {name}: {exc}')
            continue
        if actual != expected_bytes:
            errors.append(f'Launcher differs from canonical generated plan: {name}')
    if {p.name for p in d.glob('launch-host-*.sh')} != set(canonical):
        errors.append('Missing or unexpected launcher')
    return errors


def _sha(path):
    h = hashlib.sha256()
    with pathlib.Path(path).open('rb') as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def _shard_paths(folder):
    d = pathlib.Path(folder)
    if d.is_symlink() or not d.is_dir():
        raise ValueError('Shard path must be a real directory')
    summary = d / 'summary.json'
    if summary.is_symlink() or not summary.is_file():
        raise ValueError('Missing/unsafe summary')
    info = _json(summary)
    n = info['plan']['jvms']
    if type(n) is not int or not 1 <= n <= 256:
        raise ValueError('Invalid agent count')
    expected = {'summary.json'} | {f'agent-{i:03d}.tsv' for i in range(n)}
    observed = {'summary.json'} | {p.name for p in d.glob('agent-*.tsv')}
    if observed != expected:
        raise ValueError('Missing or unexpected agent records')
    for name in expected:
        p = d/name
        if p.is_symlink() or not p.is_file() or p.stat().st_size > _MAX_MEMBER:
            raise ValueError(f'Unsafe or oversized evidence member: {name}')
    if sum((d/n).stat().st_size for n in expected) > _MAX_TOTAL:
        raise ValueError('Total shard evidence size exceeded')
    return sorted(expected)


def ordered_shard_folders(folders):
    """Canonicalize evidence paths by the *declared* shard index, not argv order.

    Refuse duplicates and gaps before using positional digest entries. This
    preserves the meaning of the collector-held index when upload order varies.
    """
    if not 2 <= len(folders) <= 256:
        raise ValueError('Need 2..256 shards')
    indexed = {}
    for folder in folders:
        path = pathlib.Path(folder)
        if path.is_symlink() or not path.is_dir():
            raise ValueError('Unsafe shard directory')
        summary = path / 'summary.json'
        if summary.is_symlink() or not summary.is_file():
            raise ValueError('Unsafe/missing shard summary')
        info = _json(summary)
        plan = info.get('plan') if isinstance(info, dict) else None
        shard = plan.get('shard_index') if isinstance(plan, dict) else None
        if type(shard) is not int or not 0 <= shard < len(folders) or shard in indexed:
            raise ValueError('Duplicate, missing or invalid shard index')
        indexed[shard] = path
    if set(indexed) != set(range(len(folders))):
        raise ValueError('Incomplete shard coverage')
    return [indexed[i] for i in range(len(folders))]


def seal_shards(folders):
    """Produce a collector-held digest index; do not trust an index beside hostile inputs."""
    folders = ordered_shard_folders(folders)
    members = []
    for i, folder in enumerate(folders):
        for name in _shard_paths(folder):
            p = pathlib.Path(folder)/name
            members.append({'shard': i, 'file': name, 'size': p.stat().st_size, 'sha256': _sha(p)})
    return {'schema_version': 1, 'shards': len(folders), 'files': members,
            'notice': 'SHA256 integrity only; index must be secured separately; does not authenticate remote hosts.'}


def verify_seal(index, folders):
    index = _json(index)
    if not isinstance(index, dict) or index.get('schema_version') != 1:
        return ['Invalid evidence index schema']
    try:
        folders = ordered_shard_folders(folders)
        if type(index.get('shards')) is not int or index['shards'] != len(folders):
            raise ValueError('Shard index count mismatch')
        expected = index['files']
        if not isinstance(expected, list) or len(expected) > 66000:
            raise ValueError('Invalid evidence member list')
        found = set()
        for item in expected:
            i, name, digest, size = (item[k] for k in ('shard', 'file', 'sha256', 'size'))
            if (type(i) is not int or not 0 <= i < len(folders) or
                not isinstance(name, str) or not re.fullmatch(r'(summary\.json|agent-[0-9]{3}\.tsv)', name) or
                (i, name) in found or not isinstance(digest, str) or not _HEX.fullmatch(digest) or
                type(size) is not int or not 0 <= size <= _MAX_MEMBER):
                raise ValueError('Invalid, duplicate or unsafe index entry')
            found.add((i, name))
        actual = set()
        for i, folder in enumerate(folders):
            for name in _shard_paths(folder):
                actual.add((i, name))
        if found != actual:
            raise ValueError('Missing/extra indexed evidence member')
        for item in expected:
            p = pathlib.Path(folders[item['shard']])/item['file']
            if p.stat().st_size != item['size'] or _sha(p) != item['sha256']:
                raise ValueError('Evidence changed: shard %s %s' % (item['shard'],item['file']))
        return []
    except (KeyError, ValueError, TypeError, OSError, json.JSONDecodeError) as exc:
        return [str(exc)]


def main(argv=None):
    p=argparse.ArgumentParser(description='Independent collector shard integrity checks')
    p.add_argument('--seal',action='store_true',help='Write new evidence index; store separately from shards')
    p.add_argument('--index',required=True,type=pathlib.Path)
    p.add_argument('shards',nargs='+',type=pathlib.Path)
    a=p.parse_args(argv)
    try:
        if a.seal:
            if a.index.exists():
                raise ValueError('Refusing to overwrite existing index')
            result=seal_shards(a.shards)
            a.index.write_text(json.dumps(result,sort_keys=True,indent=2)+'\n',encoding='utf-8')
            print('EVIDENCE_INDEX_CREATED',len(result['files']))
        else:
            errors=verify_seal(a.index,a.shards)
            if errors:
                print('EVIDENCE_INDEX_REJECTED',errors,file=sys.stderr)
                return 1
            print('EVIDENCE_INDEX_VERIFIED')
        return 0
    except (ValueError, KeyError, OSError, json.JSONDecodeError) as exc:
        print('EVIDENCE_INDEX_FATAL',exc,file=sys.stderr)
        return 2


if __name__=='__main__':
    sys.exit(main())
