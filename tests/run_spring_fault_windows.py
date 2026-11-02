#!/usr/bin/env python3
"""Real Spring Boot crash-window acceptance against disposable PostgreSQL schemas.

Each case starts a fault-armed Boot JVM alone to deterministically own the job,
confirms the EXACT committed state and OS exit (81/82/83), then starts an
independent real Boot JVM on the same database schema and RabbitMQ queue.
The SQLite provider is a separate process with a durable, finite idempotency TTL.
This does NOT claim two live workers at the instant of the fault: that different
race was exercised by the multi-peer staged-crash acceptance test.
"""
import argparse
import json
import os
import pathlib
import secrets
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]
P = argparse.ArgumentParser(description=__doc__)
P.add_argument('--boot-jar', required=True)
P.add_argument('--output', type=pathlib.Path)
P.add_argument('--only', choices=('after-auth', 'after-http', 'after-observe'))
ARGS = P.parse_args()
DB_ENV = {
    'host': os.getenv('DEROYQUE_PG_HOST', '127.0.0.1'),
    'port': os.getenv('DEROYQUE_PG_PORT', '5432'),
    'db': os.getenv('DEROYQUE_PG_DB', 'deroyque_ci'),
    'user': os.getenv('DEROYQUE_PG_USER', 'deroyque_ci'),
    'pass': os.getenv('DEROYQUE_PG_PASSWORD', ''),
}
PSQL = os.getenv('DEROYQUE_PSQL', 'psql')
TOKEN_READ = secrets.token_urlsafe(30)
TOKEN_WRITE = secrets.token_urlsafe(30)
TAG = uuid.uuid4().hex[:10]
RESULT = {'suite': 'spring-real-crash-windows', 'checks': [], 'cases': [], 'runtime': 'Spring Boot actual executable JAR'}
CHILDREN = []
TMP = tempfile.TemporaryDirectory(prefix='deroyque-fault-windows-')
DIR = pathlib.Path(TMP.name)


def check(ok, label):
    ok = bool(ok)
    RESULT['checks'].append({'name': label, 'passed': ok})
    n = len(RESULT['checks'])
    print(f'{"PASS" if ok else "FAIL"} {n}/{n} {label}', flush=True)
    if not ok:
        raise AssertionError(label)


def sql(statement):
    env = dict(os.environ, PGPASSWORD=DB_ENV['pass'])
    args = [PSQL, '-X', '-A', '-t', '-v', 'ON_ERROR_STOP=1', '-h', DB_ENV['host'], '-p', DB_ENV['port'],
            '-U', DB_ENV['user'], '-d', DB_ENV['db'], '-c', statement]
    proc = subprocess.run(args, env=env, text=True, capture_output=True, timeout=18)
    if proc.returncode:
        # Database connection failures may contain secrets; show only exit metadata.
        raise RuntimeError('psql command failed (exit '+str(proc.returncode)+')')
    return proc.stdout.strip()


def port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def spawn(role, argv, environment):
    logfile = (DIR / (role+'.log')).open('w')
    proc = subprocess.Popen(argv, env={**os.environ, **environment}, stdout=logfile,
                            stderr=subprocess.STDOUT, start_new_session=True)
    CHILDREN.append((proc, logfile))
    return proc


def http(endpoint, verb='GET', body=None, token=None):
    headers = {}
    if token is not None:
        headers['Authorization'] = 'Bearer '+token
    if body is not None:
        headers['Content-Type'] = 'application/json'
        body = json.dumps(body, separators=(',', ':')).encode('utf-8')
    req = urllib.request.Request(endpoint, data=body, method=verb, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=4) as r:
            return r.status, r.read().decode('utf-8')
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8')


def until(predicate, limit=50, interval=.18):
    deadline = time.monotonic()+limit
    while time.monotonic() < deadline:
        try:
            value = predicate()
            if value:
                return value
        except (OSError, ValueError, urllib.error.URLError, TimeoutError):
            pass
        time.sleep(interval)
    return None


def count_effects(key):
    with sqlite3.connect(DIR / 'provider.sqlite3', timeout=12) as c:
        return c.execute('SELECT count(*) FROM effect WHERE key=?', (key,)).fetchone()[0]


def count_posts(key):
    # This is a diagnostic/secondary assertion; SQLite's durable effect count
    # remains the authoritative business-effect measure.
    data = (DIR / 'provider.log').read_text(errors='replace')
    return data.count('DURABLE_EFFECT '+key+' generation=')


def boot_env(schema, queue, srv_port, provider_port):
    url = (f'jdbc:postgresql://{DB_ENV["host"]}:{DB_ENV["port"]}/'
           f'{DB_ENV["db"]}?currentSchema={schema}')
    return {
        'P13_JDBC': url, 'P13_DB_USER': DB_ENV['user'], 'P13_DB_PASSWORD': DB_ENV['pass'],
        'SPRING_DATASOURCE_URL': url, 'SPRING_DATASOURCE_USERNAME': DB_ENV['user'],
        'SPRING_DATASOURCE_PASSWORD': DB_ENV['pass'],
        'SPRING_FLYWAY_SCHEMAS': schema, 'SPRING_FLYWAY_DEFAULT_SCHEMA': schema,
        'P13_QUEUE': queue, 'P13_API_PORT': str(srv_port),
        'SERVER_PORT': str(srv_port), 'SERVER_ADDRESS': '127.0.0.1',
        'P13_PROVIDER_URL': f'http://127.0.0.1:{provider_port}/activate',
        'P19_RECONCILE_URL': f'http://127.0.0.1:{provider_port}/lookup',
        # Explicit finite retention, strictly less than the provider's real TTL.
        'P16_PROVIDER_RETENTION_MS': '300000', 'P20_SIGNED_LOOKUP_REQUIRED': '1',
        'P20_PUBLIC_KEY_PEM': str(ROOT/'fixtures/keys/provider-test-public.pem'),
        'P20_EXPECTED_ISSUER': 'deroyque-lab-provider', 'P20_EXPECTED_KID': 'lab-key-01',
        'APP_READ_TOKEN': TOKEN_READ, 'APP_WRITE_TOKEN': TOKEN_WRITE,
        'APP_LAB_METRICS_ENABLED': '0', 'APP_AUTH_MODE': 'lab', 'APP_API_ENABLED': '1',
    }


def observe(schema, key):
    # The caller generates all identifiers. No untrusted SQL interpolation.
    statement = (f"SELECT r.stage, "
                 f"(SELECT count(*) FROM {schema}.p9_dispatch d WHERE d.rid=r.rid), "
                 f"(SELECT count(*) FROM {schema}.p9_observation o JOIN {schema}.p9_dispatch d ON d.did=o.did WHERE d.rid=r.rid), "
                 f"(SELECT count(*) FROM {schema}.p20_signed_attestation a JOIN {schema}.p9_dispatch d ON d.did=a.did WHERE d.rid=r.rid), "
                 f"(SELECT count(*) FROM {schema}.p9_settlement s WHERE s.rid=r.rid) "
                 f"FROM {schema}.p9_requests r WHERE r.idem_key='{key}'")
    answer = sql(statement)
    if not answer:
        return None
    parts = answer.split('|')
    if len(parts) != 5:
        raise ValueError('unexpected DB snapshot format')
    return dict(stage=parts[0], dispatches=int(parts[1]),
                observations=int(parts[2]), signed=int(parts[3]), settlements=int(parts[4]))


def run():
    jar = pathlib.Path(ARGS.boot_jar).resolve()
    if not jar.is_file() or jar.stat().st_size < 100000:
        print('BLOCKED: actual Spring Boot executable JAR required; no stand-in accepted.', flush=True)
        return 3
    prov_port = port()
    provider = spawn('provider', ['python3', '-u', str(ROOT/'provider/independent_provider.py')], {
        'P19_PROVIDER_PORT': str(prov_port), 'P19_PROVIDER_SQLITE': str(DIR/'provider.sqlite3'),
        'P19_PROVIDER_RETENTION_MS': '600000',
        'P20_PRIVATE_KEY_PEM': str(ROOT/'fixtures/keys/provider-test-private.pem'),
        'P20_ISSUER': 'deroyque-lab-provider', 'P20_KID': 'lab-key-01',
    })
    check(bool(until(lambda: provider.poll() is None and http(f'http://127.0.0.1:{prov_port}/health')[0] == 200, 15)),
          'durable independent SQLite provider is ready')
    cases = [ARGS.only] if ARGS.only else ['after-auth', 'after-http', 'after-observe']
    flags = {'after-auth': ('P13_CRASH_AFTER_AUTH_ONCE', 81, 'P13_FAULT halt after durable authorization'),
             'after-http': ('P13_CRASH_AFTER_HTTP_ONCE', 82, 'P13_FAULT halt after provider response before observation'),
             'after-observe': ('P14_CRASH_AFTER_OBSERVE_ONCE', 83, 'P14_FAULT crashed after durable observation before adoption')}
    for idx, name in enumerate(cases):
        flag, expected_exit, marker = flags[name]
        schema = f'm15_{idx}_{TAG}'
        queue = f'm15.fault.{idx}.{TAG}'
        key = f'm15-{TAG}-{name}'
        sql('CREATE SCHEMA '+schema)
        srv_port = port()
        env = boot_env(schema, queue, srv_port, prov_port)
        victim_role = f'{name}-victim'
        victim = spawn(victim_role, ['java', '-Xmx384m', '-jar', str(jar)], {**env, flag: '1'})
        def ready():
            if victim.poll() is not None:
                return False
            return http(f'http://127.0.0.1:{srv_port}/api/v1/health/readiness')[0] == 200
        check(bool(until(ready, 55)), name+': real Spring victim ready against fresh schema')
        history = sql(f'SELECT version FROM {schema}.flyway_schema_history WHERE success ORDER BY installed_rank')
        check(history.splitlines() == ['1', '2'], name+': real Flyway V1/V2 installed on fresh schema')
        payload = {'externalId': key, 'customerId': 'c-fault-window', 'productCode': 'p-fault-window'}
        code, data = http(f'http://127.0.0.1:{srv_port}/api/v1/requests', 'POST', payload, TOKEN_WRITE)
        rid = json.loads(data).get('id') if code == 202 else None
        check(code == 202 and isinstance(rid, str) and len(rid) == 36, name+': durable HTTP admission 202')
        check(bool(until(lambda: victim.poll() is not None, 40)),
              name+': JVM actually exited at injected crash boundary')
        check(victim.returncode == expected_exit, name+': exact Runtime.halt exit code '+str(expected_exit))
        message = (DIR/(victim_role+'.log')).read_text(errors='replace')
        check(marker in message, name+': exact crash marker recorded by faulted JVM')
        before = observe(schema, key)
        effects_before = count_effects(key)
        check(before is not None and before['stage'] == 'IN_FLIGHT' and before['dispatches'] == 1 and before['settlements'] == 0,
              name+': durable authorization exists but settlement has not committed')
        expected_observations = 1 if name == 'after-observe' else 0
        check(before['observations'] == expected_observations and before['signed'] == expected_observations,
              name+': exact observation/evidence boundary is durable')
        expected_effect = 0 if name == 'after-auth' else 1
        check(effects_before == expected_effect, name+': external effect count at crash equals '+str(expected_effect))
        posts_at_crash = count_posts(key)
        # Do NOT reuse the victim process or start the JDK HTTP adapter.
        survivor_port = port()
        survivor = spawn(f'{name}-survivor', ['java', '-Xmx384m', '-jar', str(jar)],
                         {**boot_env(schema, queue, survivor_port, prov_port)})
        check(bool(until(lambda: survivor.poll() is None and
              http(f'http://127.0.0.1:{survivor_port}/api/v1/health/readiness')[0] == 200, 55)),
              name+': independent Spring JVM restarted workers on existing schema and queue')
        def terminal():
            if survivor.poll() is not None:
                return None
            current = observe(schema, key)
            return current if current and current['stage'] == 'DONE' else None
        final = until(terminal, 80, .35)
        check(final is not None, name+': surviving Spring JVM finalized request as DONE')
        check(count_effects(key) == 1, name+': provider persisted exactly one external effect')
        check(final['signed'] >= 1 and final['settlements'] >= 1,
              name+': signed evidence and durable settlement available after recovery')
        if name == 'after-observe':
            check(count_posts(key) == posts_at_crash,
                  name+': recorded signed proof recovered without any additional provider POST')
        code, replay = http(f'http://127.0.0.1:{survivor_port}/api/v1/requests', 'POST', payload, TOKEN_WRITE)
        check(code == 200 and json.loads(replay).get('id') == rid,
              name+': idempotent HTTP replay returns the original UUID after JVM loss')
        RESULT['cases'].append({'window': name, 'victim_exit_code': expected_exit,
                                'state_at_crash': before, 'effect_at_crash': effects_before,
                                'state_after_recovery': final, 'effect_after_recovery': count_effects(key),
                                'new_effects': count_effects(key)-effects_before,
                                'provider_posts_at_crash': posts_at_crash,
                                'provider_posts_after_recovery': count_posts(key)})
        # Clean up ONLY this disposable survivor; shared PG/Rabbit stay running.
        os.killpg(survivor.pid, signal.SIGTERM)
        try:
            survivor.wait(timeout=8)
        except subprocess.TimeoutExpired:
            os.killpg(survivor.pid, signal.SIGKILL)
            survivor.wait()
    return 0


def main():
    code = 1
    try:
        code = run()
    except Exception as exc:
        RESULT['error_type'] = type(exc).__name__
        # Avoid raw HTTP/SQL/token/URI exception strings from test framework.
        print('FAULT_WINDOWS_ERROR '+type(exc).__name__, flush=True)
    finally:
        for proc, logfile in reversed(CHILDREN):
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                    proc.wait(timeout=5)
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    if proc.poll() is None:
                        os.killpg(proc.pid, signal.SIGKILL)
                        proc.wait()
            logfile.close()
        failures = sum(not c['passed'] for c in RESULT['checks'])
        total = len(RESULT['checks'])
        RESULT.update({'failed': failures, 'passed': total-failures, 'total': total, 'exit_code': code})
        print(f'FAULT_WINDOWS_RESULT FAILURES {failures}/{total} PASSED {total-failures}/{total}', flush=True)
        if ARGS.output:
            ARGS.output.parent.mkdir(parents=True, exist_ok=True)
            ARGS.output.write_text(json.dumps(RESULT, indent=2, sort_keys=True)+'\n')
        TMP.cleanup()
    return code


if __name__ == '__main__':
    sys.exit(main())
