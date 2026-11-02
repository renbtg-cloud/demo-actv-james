#!/usr/bin/env python3
"""Fetch opt-in read-only admission metrics from a single service JVM.

An observation is sourced from the service's monotonic clock; unlike producer wall
clocks, all events in one JVM share this clock. It does not measure external effects.
"""
import argparse
import json
import os
import pathlib
import sys
import urllib.error
import urllib.parse
import urllib.request


def fetch(url,run_id,token,allow_remote=False,timeout=8):
    if not run_id or len(run_id)>48 or not all(c.isalnum() and c.isascii() or c in '_-' for c in run_id):
        raise ValueError('Run ID must match [A-Za-z0-9_-]{1,48}')
    u=urllib.parse.urlsplit(url)
    if u.scheme not in ('http','https') or not u.hostname or u.username or u.password or u.query or u.fragment or u.path not in ('','/'):
        raise ValueError('Expected plain http(s) base URL without embedded secrets')
    loopback=u.hostname in ('127.0.0.1','localhost','::1')
    if not loopback and (not allow_remote or u.scheme!='https'):
        raise ValueError('Remote metrics require --allow-remote and HTTPS; tunnel through loopback otherwise')
    if not token:raise ValueError('Set DEROYQUE_SIM_READ_TOKEN securely')
    request=urllib.request.Request(url.rstrip('/')+'/api/v1/lab/ingress-metrics?run='+run_id,
        headers={'Authorization':'Bearer '+token,'Accept':'application/json'})
    with urllib.request.urlopen(request,timeout=timeout) as response:
        result=json.load(response)
    if result.get('run_id')!=run_id or result.get('schema_version')!=1:
        raise ValueError('Unexpected metrics identity or schema version')
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description='Capture service JVM ingress metrics for a distributed workload')
    parser.add_argument('--url',required=True);parser.add_argument('--run-id',required=True)
    parser.add_argument('--output',type=pathlib.Path,required=True)
    parser.add_argument('--allow-remote',action='store_true')
    a=parser.parse_args(argv)
    try:
        if a.output.exists():raise ValueError('Output already exists; never overwrite measurement evidence')
        data=fetch(a.url,a.run_id,os.environ.get('DEROYQUE_SIM_READ_TOKEN'),a.allow_remote)
        a.output.parent.mkdir(parents=True,exist_ok=True)
        with a.output.open('x',encoding='utf-8') as file:json.dump(data,file,indent=2,sort_keys=True)
        print(f'SERVER_METRICS_CAPTURED run={a.run_id} started={data["started"]} finished={data["finished"]} file={a.output}')
        return 0
    except (ValueError,KeyError,OSError,urllib.error.URLError,json.JSONDecodeError) as exc:
        print(f'SERVER_METRICS_ERROR: {exc}',file=sys.stderr);return 2

if __name__=='__main__':sys.exit(main())
