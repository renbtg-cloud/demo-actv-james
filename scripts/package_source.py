#!/usr/bin/env python3
"""Create and independently verify bounded, Git-committed, deterministic source ZIPs.

Never treat an internally consistent checksum manifest as proof of origin. The
consumer must independently check out and compare the exact commit.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import zipfile

MANIFEST = 'MANIFEST-SHA256.txt'
META = 'PACKAGE-SHA256.json'
FORMAT = 'deroyque-git-source-v2'
EPOCH = (1980, 1, 1, 0, 0, 0)
MAX_ENTRIES = 20_000
MAX_FILE_SIZE = 64 * 1024 * 1024
MAX_TOTAL_SIZE = 512 * 1024 * 1024
MAX_ARCHIVE_SIZE = 512 * 1024 * 1024
MAX_COMPRESSION_RATIO = 500
MAX_METADATA_SIZE = 8 * 1024 * 1024
CHUNK = 64 * 1024


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], capture_output=True,
                          check=True, timeout=30).stdout


def safe_name(name):
    parsed = PurePosixPath(name)
    if (not name or name.startswith('/') or name in (MANIFEST, META) or
            '..' in parsed.parts or '\\' in name or parsed.as_posix() != name or
            any(ord(c) < 32 or ord(c) == 127 for c in name)):
        raise ValueError('unsafe or reserved source path')
    return name


def tracked_modes(root):
    entries = git(root, 'ls-files', '--stage', '-z').split(b'\0')
    modes = {}
    for record in entries:
        if not record:
            continue
        header, name_bytes = record.split(b'\t', 1)
        mode, _blob_sha, stage = header.split()
        if stage != b'0' or mode not in (b'100644', b'100755'):
            raise ValueError('unmerged or nonregular Git entry is unsupported')
        name = safe_name(name_bytes.decode('utf-8'))
        if name in modes:
            raise ValueError('duplicate Git entry')
        modes[name] = mode.decode('ascii')
    if not modes or len(modes) > MAX_ENTRIES:
        raise ValueError('empty or excessive Git file list')
    for name, mode in modes.items():
        path = root / name
        if (path.is_symlink() or not path.is_file() or
                not path.resolve().is_relative_to(root) or
                ('100755' if path.stat().st_mode & 0o111 else '100644') != mode):
            raise ValueError('worktree file differs in type or executable mode')
    return dict(sorted(modes.items()))


def tracked_files(root):
    return list(tracked_modes(root))


def ensure_clean_checkout(root):
    git(root, 'rev-parse', '--verify', 'HEAD')
    if git(root, 'status', '--porcelain=v1', '-z', '--untracked-files=no'):
        raise ValueError('tracked Git changes not committed: refuse misleading source package')


def sha256_file(path):
    with path.open('rb') as stream:
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(CHUNK), b''):
            digest.update(chunk)
        return digest.hexdigest()


def member(name, mode):
    info = zipfile.ZipInfo(name, EPOCH)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = ((stat.S_IFREG | (0o755 if mode == '100755' else 0o644)) << 16)
    return info


def make_archive(root, output):
    root = root.resolve()
    output = output.resolve()
    if output.is_relative_to(root):
        raise ValueError('ZIP output must be outside worktree')
    ensure_clean_checkout(root)
    modes = tracked_modes(root)
    sizes = [(name, (root / name).stat().st_size) for name in modes]
    if any(size > MAX_FILE_SIZE for _, size in sizes) or sum(size for _, size in sizes) > MAX_TOTAL_SIZE:
        raise ValueError('source tree exceeds bounded packaging budget')
    sums = {name: sha256_file(root / name) for name in modes}
    manifest = ''.join(f'{sums[n]}  {n}\n' for n in modes).encode('utf-8')
    meta = (json.dumps({'format': FORMAT, 'files': sums, 'modes': modes},
                       sort_keys=True, indent=2) + '\n').encode('utf-8')
    if max(len(meta), len(manifest)) > MAX_METADATA_SIZE:
        raise ValueError('metadata exceeds bounded packaging budget')
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED,
                         compresslevel=9, allowZip64=False) as archive:
        for name in modes:
            with (root / name).open('rb') as original, archive.open(member(name, modes[name]), 'w', force_zip64=False) as dest:
                shutil.copyfileobj(original, dest, CHUNK)
        archive.writestr(member(MANIFEST, '100644'), manifest)
        archive.writestr(member(META, '100644'), meta)
    if output.stat().st_size > MAX_ARCHIVE_SIZE:
        raise ValueError('compressed archive exceeds size budget')
    # Detect source changes during archive generation, even when the resulting
    # archive and its self-declared checksums are internally consistent.
    ensure_clean_checkout(root)
    return verify_against_checkout(root, output)


def _unique_json_pairs(pairs):
    data = {}
    for key, value in pairs:
        if key in data:
            raise ValueError('duplicate JSON metadata key')
        data[key] = value
    return data


def _zip_stream_hash(archive, name):
    digest = hashlib.sha256()
    with archive.open(name, 'r') as stream:
        for block in iter(lambda: stream.read(CHUNK), b''):
            digest.update(block)
    return digest.hexdigest()


def verify_archive(path):
    path = Path(path)
    if path.stat().st_size > MAX_ARCHIVE_SIZE:
        raise ValueError('compressed ZIP exceeds bounded budget')
    with zipfile.ZipFile(path, 'r', allowZip64=False) as archive:
        entries = archive.infolist()
        names = [entry.filename for entry in entries]
        if len(entries) < 3 or len(entries) > MAX_ENTRIES + 2 or len(names) != len(set(names)):
            raise ValueError('missing or duplicated/excessive ZIP entries')
        if MANIFEST not in names or META not in names:
            raise ValueError('missing ZIP manifest or metadata')
        total = 0
        for entry in entries:
            safe_name(entry.filename) if entry.filename not in (MANIFEST, META) else None
            if entry.filename in (MANIFEST, META) and entry.file_size > MAX_METADATA_SIZE:
                raise ValueError('oversized manifest or metadata')
            if (entry.flag_bits & 0x1 or entry.compress_type != zipfile.ZIP_DEFLATED or
                    entry.create_system != 3):
                raise ValueError('unexpected archive encryption/compression/platform')
            mode = entry.external_attr >> 16
            if stat.S_IFMT(mode) != stat.S_IFREG or stat.S_IMODE(mode) not in (0o644, 0o755):
                raise ValueError('unsafe ZIP file type or permission bits')
            if entry.filename in (MANIFEST, META) and stat.S_IMODE(mode) != 0o644:
                raise ValueError('metadata file has unexpected mode')
            if entry.file_size > MAX_FILE_SIZE and entry.filename not in (META, MANIFEST):
                raise ValueError('oversized ZIP entry')
            total += entry.file_size
            if total > MAX_TOTAL_SIZE:
                raise ValueError('excessive expanded ZIP size')
            if entry.file_size > 8192 and entry.file_size > MAX_COMPRESSION_RATIO * max(1, entry.compress_size):
                raise ValueError('suspicious compression expansion ratio')
        raw = archive.read(META)
        meta = json.loads(raw, object_pairs_hook=_unique_json_pairs)
        if (not isinstance(meta, dict) or set(meta) != {'format', 'files', 'modes'} or
                meta['format'] != FORMAT or not isinstance(meta['files'], dict) or
                not isinstance(meta['modes'], dict)):
            raise ValueError('invalid ZIP source provenance metadata')
        if (json.dumps(meta, sort_keys=True, indent=2) + '\n').encode('utf-8') != raw:
            raise ValueError('noncanonical metadata JSON')
        sums, modes = meta['files'], meta['modes']
        if not sums or set(sums) != set(modes) or set(names) != set(sums) | {MANIFEST, META}:
            raise ValueError('source list mismatch')
        for name, expected in sums.items():
            safe_name(name)
            if (not isinstance(expected, str) or re.fullmatch('[0-9a-f]{64}', expected) is None or
                    modes[name] not in ('100644', '100755')):
                raise ValueError('invalid source hash or Git mode')
            actual_mode = '100755' if stat.S_IMODE(archive.getinfo(name).external_attr >> 16) == 0o755 else '100644'
            if modes[name] != actual_mode:
                raise ValueError('source executable mode mismatches manifest')
            if _zip_stream_hash(archive, name) != expected:
                raise ValueError('source content hash mismatch')
        canonical = ''.join(f'{sums[n]}  {n}\n' for n in sorted(sums)).encode('utf-8')
        if archive.read(MANIFEST) != canonical:
            raise ValueError('source manifest text mismatch')
    return {'files': len(sums), 'sha256': sha256_file(path)}


def verify_against_checkout(root, archive):
    root = Path(root).resolve()
    ensure_clean_checkout(root)
    reference = tracked_modes(root)
    result = verify_archive(archive)
    with zipfile.ZipFile(archive, 'r') as z:
        if set(z.namelist()) != set(reference) | {MANIFEST, META}:
            raise ValueError('published archive differs from checkout file set')
        for name, mode in reference.items():
            archived = z.getinfo(name)
            if ('100755' if stat.S_IMODE(archived.external_attr >> 16) == 0o755 else '100644') != mode:
                raise ValueError('published ZIP mode differs from Git index')
            if sha256_file(root / name) != _zip_stream_hash(z, name):
                raise ValueError('published ZIP content differs from checkout')
    ensure_clean_checkout(root)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--verify-only', action='store_true')
    parser.add_argument('--verify-checkout', action='store_true')
    args = parser.parse_args()
    try:
        report = (verify_against_checkout(args.root, args.output) if args.verify_checkout else
                  verify_archive(args.output) if args.verify_only else make_archive(args.root, args.output))
        print('SOURCE_PACKAGE_RESULT FAILURES 0/' + str(report['files']) +
              ' PASSED ' + str(report['files']) + '/' + str(report['files']) +
              ' sha256=' + report['sha256'])
    except (OSError, ValueError, subprocess.CalledProcessError, subprocess.TimeoutExpired,
            zipfile.BadZipFile, zipfile.LargeZipFile, UnicodeError) as err:
        print('SOURCE_PACKAGE_FAILED ' + type(err).__name__, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
