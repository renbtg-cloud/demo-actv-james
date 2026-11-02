#!/usr/bin/env python3
"""Differential, deterministic restricted-JSON tests: Python vs real Java parser.

Python's stdlib is an independent syntax decoder, NOT an oracle for the app's
field contract or policy: reject unpaired surrogates and duplicate keys here.
"""
import argparse
import base64
import json
import random
import subprocess
import sys

SEED=20261102
KEYS={'externalId','customerId','productCode'}
ALPHABET=['A','z','9','"','\\','\n','\t','\u007f','\u0080','é','\u0301','€','🚀','😀','\U0010ffff']

def strict_pairs(items):
    found={}
    for k,v in items:
        if k in found:
            raise ValueError('duplicate decoded JSON field')
        found[k]=v
    return found

def oracle(source):
    try:
        value=json.loads(source,object_pairs_hook=strict_pairs)
        if not isinstance(value,dict) or value.keys()!=KEYS:
            return 'ERR'
        if any(not isinstance(v,str) for v in value.values()):return 'ERR'
        if any(0xD800<=ord(ch)<=0xDFFF for v in value.values() for ch in v):return 'ERR'
        return 'OK\t'+base64.b64encode(value['externalId'].encode('utf-8')).decode()
    except (UnicodeError,ValueError,TypeError):
        return 'ERR'

def cases():
    rng=random.Random(SEED)
    for i in range(1200):
        text=''.join(rng.choice(ALPHABET) for _ in range(1+rng.randrange(24)))
        if i%2==0:
            yield json.dumps(dict(externalId=text,customerId='customer',productCode='code'),ensure_ascii=False,separators=(',',':'))
        else:
            yield json.dumps(dict(externalId=text,customerId='customer',productCode='code'),ensure_ascii=True,separators=(',',':'))
    for i in range(100):
        yield '{"externalId":"k'+str(i)+'","exter\\u006ealId":"k",'+'"customerId":"c","productCode":"p"}'
    for i in range(100):
        yield '{"externalId":"\\uD'+('800' if i%2==0 else 'C00')+'","customerId":"c","productCode":"p"}'
    for i in range(100):
        yield '{"externalId":"broken-'+str(i)+'","customerId":"c","productCode":"p"'


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--classes',required=True)
    a=p.parse_args()
    vectors=list(cases())
    expected=list(map(oracle,vectors))
    cmd=['java','-cp',a.classes,'demo.StrictJsonOracle']
    proc=subprocess.run(cmd,input='\n'.join(vectors)+'\n',text=True,encoding='utf-8',
                        capture_output=True,timeout=35)
    actual=proc.stdout.splitlines()
    mismatches=[i for i,(got,want) in enumerate(zip(actual,expected)) if got!=want]
    failures=len(mismatches)+abs(len(actual)-len(expected))+(proc.returncode!=0)
    print('JSON_DIFFERENTIAL_RESULT FAILURES '+str(failures)+'/'+str(len(expected))+
          ' PASSED '+str(len(expected)-len(mismatches))+'/'+str(len(expected))+
          ' java_exit='+str(proc.returncode)+' seed='+str(SEED),flush=True)
    # Do not print token contents or user data; tests use synthetic values.
    if mismatches:print('Mismatch indices (first 12): '+str(mismatches[:12]),file=sys.stderr)
    return 1 if failures else 0

if __name__=='__main__':sys.exit(main())
