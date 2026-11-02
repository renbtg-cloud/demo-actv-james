#!/usr/bin/env python3
"""Bounded, non-executable host evidence transport for offline trusted collectors.

A bundle is *not* an authentication boundary: the collector separately verifies
session-pinned Ed25519 signatures after import, using collector-held trusted keys.
Never transfer private signing keys. Only ZIP_STORED with fixed safe entry names.
"""
import argparse
import hashlib
import json
import os
import pathlib
import re
import shutil
import stat
import sys
import tempfile
import zipfile

from host_signed_evidence import _folder_payload, _read_bounded
from host_session import canonical

_MAX_AGENT = 128 * 1024 * 1024
_MAX_SUMMARY = 2 * 1024 * 1024
_MAX_CLOCK = 512 * 1024
_MAX_ENVELOPE = 64 * 1024
_MAX_TOTAL = 512 * 1024 * 1024
_MAX_ARCHIVE = _MAX_TOTAL + 4 * 1024 * 1024
_AGENT = re.compile(r'agent-[0-9]{3}\.tsv\Z')
_HEX = re.compile(r'[a-f0-9]{64}\Z')


def _safe_member(path, limit):
    p = pathlib.Path(path)
    st = p.lstat()
    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_size > limit:
        raise ValueError('Evidence member must be a regular unlinked file of bounded size')
    return st.st_size


def _hash_file(path):
    dig = hashlib.sha256()
    with open(path, 'rb') as f:
        while b := f.read(65536):
            dig.update(b)
    return dig.hexdigest()


def _manifest_members(folder, envelope, clock):
    p = pathlib.Path(folder)
    if p.is_symlink() or not p.is_dir():
        raise ValueError('Unsafe shard directory')
    _safe_member(p / 'summary.json', _MAX_SUMMARY)
    raw = _read_bounded(p / 'summary.json', _MAX_SUMMARY)
    info = json.loads(raw)
    if not isinstance(info, dict) or not isinstance(info.get('plan'), dict):
        raise ValueError('Invalid shard summary')
    idx = info['plan'].get('shard_index')
    n = info['plan'].get('jvms')
    if type(idx) is not int or not 0 <= idx < 256 or type(n) is not int or not 1 <= n <= 256:
        raise ValueError('Invalid shard index or JVM count')
    run_id = info.get('run_id')
    if not isinstance(run_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,48}', run_id):
        raise ValueError('Invalid run ID')
    agent_names = [f'agent-{i:03d}.tsv' for i in range(n)]
    observed = {q.name for q in p.iterdir() if q.name.startswith('agent-')}
    if observed != set(agent_names):
        raise ValueError('Missing or unexpected agent members')
    members = {'summary.json': p / 'summary.json'}
    members.update({name: p / name for name in agent_names})
    members['signed-envelope.json'] = pathlib.Path(envelope)
    if clock is not None:
        members['clock-report.json'] = pathlib.Path(clock)
    for name, path in members.items():
        _safe_member(path, _MAX_AGENT if _AGENT.fullmatch(name) else
                     (_MAX_SUMMARY if name == 'summary.json' else
                      _MAX_ENVELOPE if name == 'signed-envelope.json' else _MAX_CLOCK))
    if sum(path.stat().st_size for path in members.values()) > _MAX_TOTAL:
        raise ValueError('Evidence bundle uncompressed payload exceeds bound')
    # The envelope must at least identify the same run/shard before we export.
    e = json.loads(_read_bounded(envelope, _MAX_ENVELOPE))
    payload = e.get('payload') if isinstance(e, dict) else None
    if not isinstance(payload, dict) or payload.get('run_id') != run_id or type(payload.get('shard_index')) is not int or payload['shard_index'] != idx:
        raise ValueError('Signed envelope contradicts shard folder')
    if clock is not None:
        c = json.loads(_read_bounded(clock, _MAX_CLOCK))
        if not isinstance(c, dict) or c.get('run_id') != run_id or type(c.get('shard_index')) is not int or c['shard_index'] != idx:
            raise ValueError('Clock report contradicts shard folder')
    return run_id, idx, members


def bundle(folder, envelope, destination, clock=None):
    run_id, idx, members = _manifest_members(folder, envelope, clock)
    dest = pathlib.Path(destination)
    if dest.exists() or dest.is_symlink() or not dest.parent.is_dir() or dest.parent.is_symlink():
        raise ValueError('Destination must be absent in a real parent directory')
    indexed = {name: {'sha256': _hash_file(path), 'size': path.stat().st_size}
               for name, path in sorted(members.items())}
    manifest = {'schema_version': 1, 'run_id': run_id, 'shard_index': idx,
                'members': indexed, 'claim': 'SHA256 transport integrity, NOT host authentication'}
    # Exclusive create; leave no half-written archive on failure.
    with dest.open('xb') as out:
        os.fchmod(out.fileno(), 0o600)
        try:
            with zipfile.ZipFile(out, 'w', compression=zipfile.ZIP_STORED, allowZip64=True) as z:
                for name, path in sorted(members.items()):
                    item = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
                    item.compress_type = zipfile.ZIP_STORED
                    item.external_attr = (stat.S_IFREG | 0o600) << 16
                    with z.open(item, 'w', force_zip64=True) as target, path.open('rb') as source:
                        shutil.copyfileobj(source, target, 65536)
                item = zipfile.ZipInfo('bundle-manifest.json', (1980, 1, 1, 0, 0, 0))
                item.external_attr = (stat.S_IFREG | 0o600) << 16
                z.writestr(item, canonical(manifest) + b'\n')
            # Concurrent mutation while exporting must never silently pass.
            if any(_hash_file(path) != indexed[name]['sha256'] or
                   path.stat().st_size != indexed[name]['size'] for name,path in members.items()):
                raise ValueError('Shard files changed during export')
        except BaseException:
            out.close()
            dest.unlink(missing_ok=True)
            raise
    return manifest


def inspect_archive(archive):
    p = pathlib.Path(archive)
    _safe_member(p, _MAX_ARCHIVE)
    with zipfile.ZipFile(p) as z:
        entries = z.infolist()
        if len(entries) < 4 or len(entries) > 260:
            raise ValueError('Invalid bundle entry count')
        names = [e.filename for e in entries]
        if len(set(names)) != len(names) or 'bundle-manifest.json' not in names:
            raise ValueError('Duplicate/missing bundle entries')
        for item in entries:
            n = item.filename
            if (n not in {'summary.json', 'signed-envelope.json', 'clock-report.json','bundle-manifest.json'}
                and not _AGENT.fullmatch(n)):
                raise ValueError('Unsafe/unknown bundle member name')
            mode = item.external_attr >> 16
            if (item.compress_type != zipfile.ZIP_STORED or item.file_size != item.compress_size or
                item.flag_bits & 1 or not stat.S_ISREG(mode) or item.file_size >
                (_MAX_AGENT if _AGENT.fullmatch(n) else
                 _MAX_SUMMARY if n in ('summary.json', 'bundle-manifest.json') else
                 _MAX_ENVELOPE if n == 'signed-envelope.json' else _MAX_CLOCK)):
                raise ValueError('Unsafe archive entry type, compression or size')
        if sum(e.file_size for e in entries) > _MAX_TOTAL + _MAX_SUMMARY:
            raise ValueError('Too much uncompressed data')
        manifest_bytes = z.read('bundle-manifest.json')
        if len(manifest_bytes) > _MAX_SUMMARY:
            raise ValueError('Oversized bundle manifest')
        manifest = json.loads(manifest_bytes)
        if not isinstance(manifest, dict) or set(manifest) != {'schema_version','run_id','shard_index','members','claim'} or type(manifest['schema_version']) is not int or manifest['schema_version'] != 1:
            raise ValueError('Invalid bundle manifest schema')
        run_id, idx = manifest['run_id'], manifest['shard_index']
        if not isinstance(run_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,48}',run_id) or type(idx) is not int or not 0 <= idx < 256:
            raise ValueError('Invalid run/shard identity')
        members = manifest['members']
        if not isinstance(members,dict) or set(members) != set(names)-{'bundle-manifest.json'}:
            raise ValueError('Missing/extra manifest member')
        if 'summary.json' not in members or 'signed-envelope.json' not in members:
            raise ValueError('Missing mandatory evidence')
        for name, att in members.items():
            entry = z.getinfo(name)
            if (not isinstance(att,dict) or set(att) != {'size','sha256'} or
                type(att['size']) is not int or att['size'] != entry.file_size or
                not isinstance(att['sha256'],str) or not _HEX.fullmatch(att['sha256'])):
                raise ValueError('Invalid member manifest entry')
        info = json.loads(z.read('summary.json'))
        if not isinstance(info,dict) or not isinstance(info.get('plan'),dict) or info.get('run_id') != run_id or type(info['plan'].get('shard_index')) is not int or info['plan']['shard_index'] != idx:
            raise ValueError('Contradictory shard summary')
        n = info['plan'].get('jvms')
        if type(n) is not int or not 1 <= n <= 256 or {name for name in members if _AGENT.fullmatch(name)} != {f'agent-{i:03d}.tsv' for i in range(n)}:
            raise ValueError('Invalid agent coverage')
        envelope = json.loads(z.read('signed-envelope.json'))
        payload = envelope.get('payload') if isinstance(envelope,dict) else None
        if not isinstance(payload,dict) or payload.get('run_id') != run_id or type(payload.get('shard_index')) is not int or payload['shard_index'] != idx:
            raise ValueError('Contradictory signed envelope')
        if 'clock-report.json' in members:
            c = json.loads(z.read('clock-report.json'))
            if not isinstance(c,dict) or c.get('run_id') != run_id or type(c.get('shard_index')) is not int or c['shard_index'] != idx:
                raise ValueError('Contradictory clock report')
        for name, att in members.items():
            h = hashlib.sha256()
            count = 0
            with z.open(name) as stream:
                while b := stream.read(65536):
                    count += len(b)
                    if count > att['size']:
                        raise ValueError('Member grew after manifest')
                    h.update(b)
            if count != att['size'] or h.hexdigest() != att['sha256']:
                raise ValueError('Transport member SHA-256 mismatch')
        return manifest


def unpack(archive, destination):
    # Validate all members before writing anything to the filesystem.
    manifest = inspect_archive(archive)
    dest=pathlib.Path(destination)
    if dest.exists() or dest.is_symlink() or not dest.parent.is_dir() or dest.parent.is_symlink():
        raise ValueError('Destination must not exist')
    dest.mkdir(mode=0o700)
    try:
        with zipfile.ZipFile(archive) as z:
            for name in sorted(manifest['members']):
                with z.open(name) as source, (dest/name).open('xb') as target:
                    os.fchmod(target.fileno(), 0o600)
                    shutil.copyfileobj(source, target, 65536)
        for name, att in manifest['members'].items():
            if (dest/name).stat().st_size != att['size'] or _hash_file(dest/name) != att['sha256']:
                raise ValueError('Changed bytes during extraction')
        return manifest
    except BaseException:
        shutil.rmtree(dest)
        raise


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    create=sub.add_parser('create')
    create.add_argument('--folder',type=pathlib.Path,required=True)
    create.add_argument('--signed-envelope',type=pathlib.Path,required=True)
    create.add_argument('--clock-report',type=pathlib.Path)
    create.add_argument('--output',type=pathlib.Path,required=True)
    verify=sub.add_parser('verify')
    verify.add_argument('--bundle',type=pathlib.Path,required=True)
    extract=sub.add_parser('extract')
    extract.add_argument('--bundle',type=pathlib.Path,required=True)
    extract.add_argument('--output',type=pathlib.Path,required=True)
    a=parser.parse_args(argv)
    try:
        if a.command=='create':
            result=bundle(a.folder,a.signed_envelope,a.output,a.clock_report)
        elif a.command=='verify':
            result=inspect_archive(a.bundle)
        else:
            result=unpack(a.bundle,a.output)
        print(json.dumps({'status':'TRANSPORT_INTEGRITY_ONLY_NOT_HOST_AUTHENTICATION',
                          'run_id':result['run_id'],'shard_index':result['shard_index']},sort_keys=True))
        return 0
    except (OSError,ValueError,KeyError,TypeError,json.JSONDecodeError,zipfile.BadZipFile,RuntimeError) as exc:
        print('BUNDLE_REJECTED',exc,file=sys.stderr)
        return 2

if __name__=='__main__':sys.exit(main())
