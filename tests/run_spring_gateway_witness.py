#!/usr/bin/env python3
"""Real two-Spring-JVM, RabbitMQ, PostgreSQL, SQLite-provider gateway-clock lab.

The HTTP witness is a third observer. Separate Python coordinators launch four
actual independent Java producer processes. Reports include exact failed/total
assertion counts and deliberately do NOT pretend separate physical hosts exist.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'simulator'))
from ingress_witness import WitnessServer, WitnessState
from distributed_merge import merge
from server_metrics import fetch

P = argparse.ArgumentParser(description=__doc__)
P.add_argument('--boot-jar', type=Path, required=True)
P.add_argument('--output', type=Path, required=True)
A = P.parse_args()
PG = {k: os.environ.get('DEROYQUE_PG_'+k.upper(),v) for k,v in {
    'host':'127.0.0.1','port':'5432','db':'deroyque_ci','user':'deroyque_ci','password':''}.items()}
SUFFIX = uuid.uuid4().hex[:10]
SCHEMA = 'gw_' + SUFFIX
QUEUE = 'gateway.' + SUFFIX
RUN = 'gw-' + SUFFIX
READ, WRITE = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
RESULT = {'scenario': 'real-gateway-witness', 'checks': [], 'completed': False,
          'producer_shards': 2, 'producer_jvms': 4, 'spring_jvms': 2}
CHILDREN = []


def check(ok, description):
    ok = bool(ok)
    RESULT['checks'].append({'name': description, 'passed': ok})
    n = len(RESULT['checks'])
    print(('PASS' if ok else 'FAIL') + f' {n}/{n} {description}', flush=True)
    if not ok:
        raise AssertionError(description)


def sql(command):
    p = subprocess.run([os.getenv('DEROYQUE_PSQL','psql'), '-X','-A','-t','-v','ON_ERROR_STOP=1',
        '-h', PG['host'], '-p', PG['port'], '-U', PG['user'], '-d', PG['db'], '-c', command],
        env={**os.environ,'PGPASSWORD':PG['password']}, text=True, capture_output=True, timeout=20)
    if p.returncode:
        raise RuntimeError('Failed PostgreSQL operation')
    return p.stdout.strip()


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        return sock.getsockname()[1]


def launch(name, argv, env, directory):
    log = (directory/(name+'.log')).open('w')
    p = subprocess.Popen(argv, env={**os.environ, **env}, stdout=log,
                         stderr=subprocess.STDOUT, start_new_session=True)
    CHILDREN.append((p,log))
    return p


def ready(fn, seconds=55):
    limit = time.monotonic()+seconds
    while time.monotonic()<limit:
        try:
            if fn():return True
        except (OSError, urllib.error.URLError, ValueError):pass
        time.sleep(.2)
    return False


def response(port, path):
    with urllib.request.urlopen(f'http://127.0.0.1:{port}'+path, timeout=2) as r:
        return r.status


def stop(child):
    if child.poll() is not None:return
    try:os.killpg(child.pid,signal.SIGTERM)
    except ProcessLookupError:return
    try:child.wait(timeout=4)
    except subprocess.TimeoutExpired:
        try:os.killpg(child.pid,signal.SIGKILL)
        except ProcessLookupError:pass
        child.wait(timeout=5)


def execute(directory):
    check(A.boot_jar.is_file(), 'actual Spring Boot executable JAR supplied')
    sql('CREATE SCHEMA '+SCHEMA)
    provider_port = port()
    provider = launch('provider', ['python3','-u',str(ROOT/'provider/independent_provider.py')], {
        'P19_PROVIDER_PORT':str(provider_port),'P19_PROVIDER_SQLITE':str(directory/'provider.sqlite3'),
        'P19_PROVIDER_RETENTION_MS':'600000',
        'P20_PRIVATE_KEY_PEM':str(ROOT/'fixtures/keys/provider-test-private.pem'),
        'P20_ISSUER':'deroyque-lab-provider','P20_KID':'lab-key-01'}, directory)
    check(ready(lambda: provider.poll() is None and response(provider_port,'/health')==200, 20),
          'independent persistent SQLite provider ready')
    jdbc = f'jdbc:postgresql://{PG["host"]}:{PG["port"]}/{PG["db"]}?currentSchema={SCHEMA}'
    spring_ports=[]; spring=[]
    for i in range(2):
        p = port();spring_ports.append(p)
        env = {'P13_JDBC':jdbc,'P13_DB_USER':PG['user'],'P13_DB_PASSWORD':PG['password'],
            'SPRING_DATASOURCE_URL':jdbc,'SPRING_DATASOURCE_USERNAME':PG['user'],
            'SPRING_DATASOURCE_PASSWORD':PG['password'],'SPRING_FLYWAY_SCHEMAS':SCHEMA,
            'SPRING_FLYWAY_DEFAULT_SCHEMA':SCHEMA, 'P13_QUEUE':QUEUE,
            'P13_API_PORT':str(p),'SERVER_PORT':str(p),'SERVER_ADDRESS':'127.0.0.1',
            'P13_PROVIDER_URL':f'http://127.0.0.1:{provider_port}/activate',
            'P19_RECONCILE_URL':f'http://127.0.0.1:{provider_port}/lookup',
            'P16_PROVIDER_RETENTION_MS':'300000','P20_SIGNED_LOOKUP_REQUIRED':'1',
            'P20_PUBLIC_KEY_PEM':str(ROOT/'fixtures/keys/provider-test-public.pem'),
            'P20_EXPECTED_ISSUER':'deroyque-lab-provider','P20_EXPECTED_KID':'lab-key-01',
            'APP_READ_TOKEN':READ,'APP_WRITE_TOKEN':WRITE,'APP_AUTH_MODE':'lab',
            'APP_API_ENABLED':'1','APP_LAB_METRICS_ENABLED':'1'}
        worker=launch(f'spring-{i}', ['java','-Xmx320m','-jar',str(A.boot_jar)],env,directory)
        spring.append(worker)
        check(ready(lambda worker=worker,p=p: worker.poll() is None and
                    response(p,'/api/v1/health/readiness')==200, 70),
              f'real Spring Boot JVM {i+1}/2 ready with PostgreSQL and RabbitMQ')
    check(spring[0].pid != spring[1].pid, 'two independent Spring Boot OS processes')
    check(sql(f'SELECT count(*) FROM {SCHEMA}.flyway_schema_history WHERE success')=='2',
          'real Flyway migrations applied by service')
    state = WitnessState(RUN, [f'http://127.0.0.1:{p}' for p in spring_ports])
    with WitnessServer(('127.0.0.1',0),state,timeout=12) as gateway:
        witness_thread = threading.Thread(target=gateway.serve_forever, daemon=True)
        witness_thread.start()
        check(response(gateway.server_port,'/__witness/health')==200,'independent witness ready')
        simulator_jar = ROOT/'simulator/build/deroyque-input-simulator.jar'
        subprocess.run([str(ROOT/'simulator/build.sh')],check=True,capture_output=True,timeout=25)
        check(simulator_jar.is_file(),'standalone Java 21 input simulator built')
        start = int(time.time()*1000)+14_000
        env={**os.environ,'DEROYQUE_SIM_WRITE_TOKEN':WRITE}
        shards=[]
        for index in range(2):
            folder=directory/f'shard-{index}'
            cmd=[sys.executable,str(ROOT/'simulator/run.py'), '--url',f'http://127.0.0.1:{gateway.server_port}',
                 '--jvms=2','--concurrency=6','--requests=80','--keys=20','--rate=50',
                 '--conflict-percent=30','--shard-count=2',f'--shard-index={index}',
                 f'--start-at-ms={start}',f'--run-id={RUN}','--output-dir',str(folder)]
            shards.append((folder,subprocess.Popen(cmd,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)))
        for folder,child in shards:
            try:
                stdout,stderr=child.communicate(timeout=70)
            except subprocess.TimeoutExpired:
                child.kill();child.communicate();raise RuntimeError('Java producer coordinator exceeded deadline')
            check(child.returncode==0 and (folder/'summary.json').exists(),
                  'independent two-JVM coordinator completed its assigned shard')
        report=state.report()
        check(report['finished']==80 and report['inflight']==0 and report['transport_errors']==0,
              'witness measured all 80 finished HTTP admissions without transport errors')
        check(all(x['finished']>=1 for x in report['backends']),
              'each independent Spring JVM handled real POST traffic')
        metrics=[fetch(f'http://127.0.0.1:{port}',RUN,READ) for port in spring_ports]
        check(len({m['instance_id'] for m in metrics})==2,
              'real per-JVM instrumentation reports two distinct service identities')
        result=merge([p for p,c in shards],server_metrics=metrics,gateway_witness=report,
                     verify_state=True,await_done_seconds=80,token=READ)
        check(result['outcome']=='PASS','global producer, witness, service counter and final DB state audits agree')
        check(result['global_recorded_posts']==80 and result['total_producer_jvms']==4,
              '80 real POSTs from four independent producer JVMs reconciled')
        check(result['gateway_witness']['backend_count']==2 and result['server_side']['instance_count']==2,
              'one-clock gateway throughput across two distinct Spring JVMs without summing JVM clocks')
        check(result['server_side']['server_post_qps'] is None and result['global_throughput_qps']>0,
              'per-service JVM QPS not added, independent gateway rate measured')
        check(result['host_evidence']['status']=='SAME_KERNEL_OBSERVED',
              'same-host experiment honestly reports same kernel, not multi-host proof')
        with sqlite3.connect(directory/'provider.sqlite3', timeout=6) as db:
            effects=db.execute('SELECT COUNT(*) FROM effect').fetchone()[0]
        check(effects==20 and result['verified_states'].get('DONE')==20,
              'exactly 20 independently stored provider effects and 20 terminal states')
        rids=f"SELECT rid FROM {SCHEMA}.p9_requests WHERE idem_key LIKE '{RUN}-%'"
        durable=int(sql(f"SELECT count(*) FROM {SCHEMA}.p9_requests WHERE idem_key LIKE '{RUN}-%'"))
        dispatches=int(sql(f'SELECT count(*) FROM {SCHEMA}.p9_dispatch WHERE rid IN ({rids})'))
        settlements=int(sql(f'SELECT count(*) FROM {SCHEMA}.p9_settlement WHERE rid IN ({rids})'))
        signed=int(sql(f'SELECT count(*) FROM {SCHEMA}.p20_signed_attestation '
                       f'WHERE did IN (SELECT did FROM {SCHEMA}.p9_dispatch WHERE rid IN ({rids}))'))
        check(durable==20 and dispatches==20 and settlements==20 and signed==20,
              '20 durable requests, external dispatches, settlements and signed attestations corroborate provider effects')
        RESULT['business_effects']=effects
        RESULT['durable_requests']=durable
        RESULT['signed_attestations']=signed
        RESULT['measurement']={k:result[k] for k in ('global_throughput_qps','gateway_witness',
                                      'host_evidence','global_recorded_posts','verified_states')}
        RESULT['completed']=True
        gateway.shutdown();witness_thread.join(timeout=3)


def main():
    code=1
    with tempfile.TemporaryDirectory(prefix='deroyque-gateway-real-') as tmp:
        try:
            execute(Path(tmp));code=0
        except Exception as exc:
            RESULT['error_type']=type(exc).__name__
            print('GATEWAY_REAL_ERROR '+type(exc).__name__,flush=True)
        finally:
            for p,log in reversed(CHILDREN):
                stop(p);log.close()
            failed=sum(not x['passed'] for x in RESULT['checks']);total=len(RESULT['checks'])
            RESULT.update({'failed':failed,'passed':total-failed,'total':total,'exit_code':code})
            print(f'GATEWAY_REAL_RESULT FAILURES {failed}/{total} PASSED {total-failed}/{total}',flush=True)
            A.output.parent.mkdir(parents=True,exist_ok=True)
            A.output.write_text(json.dumps(RESULT,indent=2,sort_keys=True)+'\n')
    return code


if __name__=='__main__':raise SystemExit(main())
