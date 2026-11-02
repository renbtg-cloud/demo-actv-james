#!/usr/bin/env python3
"""Disposable loopback UTC reference and opt-in probe client for tunneled hosts.

For remote producers, use a separately authenticated SSH tunnel to a trusted
reference and aim --probe at *local loopback*. Unauthenticated loopback HTTP and
self-reported JSON cannot attest hosts or clock trust. No credentials are handled.
"""
import argparse
import http.server
import json
import pathlib
import re
import sys
import time
import urllib.parse
import urllib.request
import urllib.error

from clock_bounds import audit_clock_probes


class ClockHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        receive = time.time_ns()
        if self.path != '/clock':
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Type','application/json')
        self.send_header('Cache-Control','no-store')
        self.end_headers()
        sent = time.time_ns()
        self.wfile.write(json.dumps({'schema_version':1,'remote_recv_ns':receive,
                                      'remote_send_ns':sent},separators=(',',':')).encode('ascii'))
    def log_message(self, format, *args):
        pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise ValueError('Clock reference redirect refused')


def probe(url, repeats=5,timeout=2):
    parsed=urllib.parse.urlsplit(url)
    if parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1','::1','localhost') or parsed.username or parsed.password or parsed.fragment or parsed.query or parsed.path != '/clock':
        raise ValueError('Use a loopback HTTP clock endpoint or an authenticated local tunnel')
    if type(repeats) is not int or not 3 <= repeats <= 64:
        raise ValueError('Clock probe count must be 3..64')
    if not 0.1 <= timeout <= 10:
        raise ValueError('Invalid clock probe timeout')
    opener=urllib.request.build_opener(_NoRedirect)
    samples=[]
    for _ in range(repeats):
        t0_wall=time.time_ns();t0_mono=time.monotonic_ns()
        with opener.open(urllib.request.Request(url,headers={'Cache-Control':'no-store'}),timeout=timeout) as reply:
            if reply.status != 200 or reply.headers.get('Content-Type') != 'application/json':
                raise ValueError('Unexpected clock response status/content-type')
            raw=reply.read(4097)
        t3_mono=time.monotonic_ns();t3_wall=time.time_ns()
        if len(raw)>4096:
            raise ValueError('Oversized clock response')
        doc=json.loads(raw)
        if doc.get('schema_version') != 1:
            raise ValueError('Invalid reference schema')
        samples.append({'local_send_ns':t0_wall,'remote_recv_ns':doc['remote_recv_ns'],
                        'remote_send_ns':doc['remote_send_ns'],'local_recv_ns':t3_wall,
                        'local_elapsed_monotonic_ns':t3_mono-t0_mono})
    return samples


def assemble(paths, run_id, reference_id, max_width_ms=100):
    shards=[]
    if not 2 <= len(paths) <= 256:
        raise ValueError('Need 2..256 producer reports')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,48}',run_id) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}',reference_id):
        raise ValueError('Invalid run/reference identity')
    for p in paths:
        p=pathlib.Path(p)
        if p.stat().st_size>512*1024 or p.is_symlink():
            raise ValueError('Unsafe/oversized clock report')
        doc=json.loads(p.read_text(encoding='utf-8'))
        if (doc.get('schema_version')!=1 or doc.get('run_id')!=run_id or doc.get('reference_id')!=reference_id):
            raise ValueError('Clock report identity mismatch')
        shards.append({'shard_index':doc['shard_index'],'samples':doc['samples']})
    shards.sort(key=lambda x:x['shard_index'])
    result={'schema_version':1,'run_id':run_id,'reference_id':reference_id,'shards':shards}
    report,errors=audit_clock_probes(result,run_id,len(shards),max_width_ms)
    if errors:
        raise ValueError('Clock report failed: '+'; '.join(errors))
    return result,report


def main(argv=None):
    p=argparse.ArgumentParser(description='Loopback/tunnel clock probe and collector')
    sub=p.add_subparsers(dest='command',required=True)
    serve=sub.add_parser('serve');serve.add_argument('--port',type=int,default=18193)
    client=sub.add_parser('probe');client.add_argument('--url',default='http://127.0.0.1:18193/clock')
    client.add_argument('--run-id',required=True);client.add_argument('--reference-id',required=True)
    client.add_argument('--shard-index',type=int,required=True);client.add_argument('--samples',type=int,default=5)
    client.add_argument('--output',type=pathlib.Path,required=True)
    collect=sub.add_parser('collect');collect.add_argument('--run-id',required=True)
    collect.add_argument('--reference-id',required=True);collect.add_argument('--max-width-ms',type=float,default=100)
    collect.add_argument('--output',type=pathlib.Path,required=True);collect.add_argument('shard_reports',nargs='+',type=pathlib.Path)
    a=p.parse_args(argv)
    try:
        if a.command=='serve':
            if not 1024<=a.port<=65535:
                raise ValueError('Port 1024..65535 required')
            http.server.ThreadingHTTPServer(('127.0.0.1',a.port),ClockHandler).serve_forever()
        elif a.command=='probe':
            if (not re.fullmatch(r'[A-Za-z0-9_-]{1,48}',a.run_id) or
                not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}',a.reference_id) or
                not 0<=a.shard_index<256):
                raise ValueError('Invalid identity/shard index')
            if a.output.exists():
                raise ValueError('Refusing to overwrite a prior clock report')
            result={'schema_version':1,'run_id':a.run_id,'reference_id':a.reference_id,
                    'shard_index':a.shard_index,'samples':probe(a.url,a.samples)}
            a.output.write_text(json.dumps(result,indent=2)+'\n')
            print('CLOCK_PROBES_CAPTURED',len(result['samples']))
        else:
            if a.output.exists():
                raise ValueError('Refusing to overwrite prior clock evidence')
            result,report=assemble(a.shard_reports,a.run_id,a.reference_id,a.max_width_ms)
            a.output.write_text(json.dumps(result,indent=2)+'\n')
            print('CLOCK_PROBES_BOUNDED',len(report['per_shard']))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print('CLOCK_PROBE_FAILED',exc,file=sys.stderr)
        return 2

if __name__=='__main__':sys.exit(main())
