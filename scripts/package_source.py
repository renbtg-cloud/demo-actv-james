#!/usr/bin/env python3
"""Produce a deterministic Git-tracked-only source ZIP with content checksums.

This utility packages the current checked-out worktree. It does not assert a
cryptographic identity for GitHub-hosted runners or sign artifacts.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys
import zipfile

MANIFEST='MANIFEST-SHA256.txt'
META='PACKAGE-SHA256.json'
EPOCH=(1980,1,1,0,0,0)


def tracked_files(root):
    process=subprocess.run(['git','-C',str(root),'ls-files','--cached','-z'],
                           capture_output=True,check=True,timeout=20)
    names=sorted(name.decode('utf-8') for name in process.stdout.split(b'\0') if name)
    if not names:raise ValueError('no Git-tracked files; refusing empty source ZIP')
    for name in names:
        path=PurePosixPath(name)
        if (not name or name.startswith('/') or '..' in path.parts or '\\' in name or
            path.as_posix()!=name or name in (MANIFEST,META)):
            raise ValueError('unsafe/reserved Git-tracked source path')
        full=root/name
        if not full.is_file() or full.is_symlink():raise ValueError('source path is not a regular file')
    return names


def member(name,data,executable=False):
    info=zipfile.ZipInfo(name,EPOCH)
    info.compress_type=zipfile.ZIP_DEFLATED
    info.create_system=3
    info.external_attr=((stat.S_IFREG | (0o755 if executable else 0o644))<<16)
    return info,data


def make_archive(root,output):
    root=root.resolve()
    if output.resolve().is_relative_to(root):
        raise ValueError('ZIP output must be outside the worktree for clear provenance')
    names=tracked_files(root)
    data={name:(root/name).read_bytes() for name in names}
    sums={name:hashlib.sha256(data[name]).hexdigest() for name in names}
    manifest=''.join(f'{sums[name]}  {name}\n' for name in names).encode('utf-8')
    meta=json.dumps({'format':'deroyque-git-source-v1','files':sums},sort_keys=True,indent=2).encode()+b'\n'
    output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for name in names:
            z.writestr(*member(name,data[name],bool((root/name).stat().st_mode&0o111)),compress_type=zipfile.ZIP_DEFLATED,compresslevel=9)
        z.writestr(*member(MANIFEST,manifest),compress_type=zipfile.ZIP_DEFLATED,compresslevel=9)
        z.writestr(*member(META,meta),compress_type=zipfile.ZIP_DEFLATED,compresslevel=9)
    return verify_archive(output)


def verify_archive(path):
    with zipfile.ZipFile(path,'r') as z:
        if z.testzip() is not None:raise ValueError('corrupted ZIP file')
        names=z.namelist()
        if len(names)!=len(set(names)) or MANIFEST not in names or META not in names:
            raise ValueError('duplicate/absent ZIP entries')
        for name in names:
            parsed=PurePosixPath(name)
            if name.startswith('/') or '..' in parsed.parts or '\\' in name or parsed.as_posix()!=name:
                raise ValueError('unsafe archive member')
        meta=json.loads(z.read(META))
        if meta.get('format')!='deroyque-git-source-v1' or not isinstance(meta.get('files'),dict):
            raise ValueError('invalid archive metadata')
        expected=meta['files']
        if not expected or set(names)!=set(expected)|{MANIFEST,META}:
            raise ValueError('ZIP contents do not match package file list')
        if any(not isinstance(h,str) or len(h)!=64 for h in expected.values()):
            raise ValueError('invalid manifest SHA-256 values')
        if any(hashlib.sha256(z.read(name)).hexdigest()!=value for name,value in expected.items()):
            raise ValueError('content hash mismatch')
        canonical=''.join(f'{expected[name]}  {name}\n' for name in sorted(expected)).encode('utf-8')
        if z.read(MANIFEST)!=canonical:raise ValueError('manifest text mismatch')
    return {'files':len(expected),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--verify-only',action='store_true')
    args=p.parse_args()
    try:
        result=verify_archive(args.output) if args.verify_only else make_archive(args.root,args.output)
        print('SOURCE_PACKAGE_RESULT FAILURES 0/'+str(result['files'])+
              ' PASSED '+str(result['files'])+'/'+str(result['files'])+
              ' sha256='+result['sha256'])
    except (OSError,ValueError,subprocess.CalledProcessError,zipfile.BadZipFile) as e:
        print('SOURCE_PACKAGE_FAILED '+type(e).__name__,file=sys.stderr)
        return 1
    return 0

if __name__=='__main__':sys.exit(main())
