#!/usr/bin/env python3
"""Milestone 19: genuine Keycloak OIDC authorization-code-independent M2M acceptance.

Spins up a disposable Keycloak 26.8.1 container, imports isolated realms with
client-credentials clients, requests Keycloak-signed access tokens via the actual
OIDC token endpoint, then exercises the actual Spring Boot resource server and
its independent PostgreSQL/RabbitMQ/SQLite provider implementation. The lab
uses HTTP on loopback only and never prints or uploads bearer tokens or secrets.
"""
import argparse
import base64
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
import urllib.parse
import urllib.request
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]
P = argparse.ArgumentParser(description=__doc__)
P.add_argument('--boot-jar', type=pathlib.Path, required=True)
P.add_argument('--output', type=pathlib.Path)
P.add_argument('--keycloak-image', default='quay.io/keycloak/keycloak:26.8.1')
P.add_argument('--use-local-keycloak', action='store_true',
               help='Exercise the first-class scripts/local_keycloak.py option rather than test-internal Docker')
args = P.parse_args()

TAG = uuid.uuid4().hex[:10]
SCHEMA = 'oidc_' + TAG
QUEUE = 'oidc.' + TAG
REALM = 'deroyque-local' if args.use_local_keycloak else 'deroyque-ci-' + TAG
OTHER_REALM = 'deroyque-local-untrusted' if args.use_local_keycloak else 'deroyque-untrusted-' + TAG
AUDIENCE = 'deroyque-activation-api'
EXTERNAL_ID = 'oidc-' + TAG + '-activation'
DBHOST = os.getenv('DEROYQUE_PG_HOST', '127.0.0.1')
DBPORT = os.getenv('DEROYQUE_PG_PORT', '5432')
DBNAME = os.getenv('DEROYQUE_PG_DB', 'deroyque_ci')
DBUSER = os.getenv('DEROYQUE_PG_USER', 'deroyque_ci')
DBPASS = os.getenv('DEROYQUE_PG_PASSWORD', '')
PSQL = os.getenv('DEROYQUE_PSQL', 'psql')
RESULT = {'suite': 'real-keycloak-oidc-client-credentials', 'checks': [], 'issuer_verified': False,
          'signed_provider_effect_verified': False, 'image': args.keycloak_image}
TMP = tempfile.TemporaryDirectory(prefix='deroyque-oidc-')
DIR = pathlib.Path(TMP.name)
CHILDREN = []
CONTAINER = 'deroyque-oidc-' + TAG


def check(ok, label):
    n = len(RESULT['checks']) + 1
    ok = bool(ok)
    RESULT['checks'].append({'name': label, 'passed': ok})
    print(f'{"PASS" if ok else "FAIL"} {n}/{n} {label}', flush=True)
    if not ok:
        raise AssertionError(label)


def freeport():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def eventually(f, seconds=45):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            v = f()
            if v:
                return v
        except (OSError, ValueError, urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            pass
        time.sleep(.3)
    return None


def request(method, url, data=None, headers=None, timeout=5):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def spawn(name, cmd, env):
    f = (DIR / (name + '.log')).open('w')
    p = subprocess.Popen(cmd, env={**os.environ, **env}, stdout=f, stderr=subprocess.STDOUT,
                         start_new_session=True)
    CHILDREN.append((p, f))
    return p


def sql(query):
    proc = subprocess.run([PSQL, '-X', '-A', '-t', '-v', 'ON_ERROR_STOP=1', '-h', DBHOST,
                           '-p', DBPORT, '-U', DBUSER, '-d', DBNAME, '-c', query],
                          env={**os.environ, 'PGPASSWORD': DBPASS}, capture_output=True,
                          text=True, timeout=25)
    if proc.returncode:
        raise RuntimeError('PostgreSQL operation failed; details retained in isolated runner')
    return proc.stdout.strip()


def scope(name):
    return {'name': name, 'protocol': 'openid-connect',
            'attributes': {'include.in.token.scope': 'true',
                           'display.on.consent.screen': 'false'}}


def audience_mapper():
    return {'name': 'deroyque-api-audience', 'protocol': 'openid-connect',
            'protocolMapper': 'oidc-audience-mapper',
            'consentRequired': False,
            'config': {'included.custom.audience': AUDIENCE,
                       'id.token.claim': 'false', 'access.token.claim': 'true'}}


def client(name, secret, permission=None, with_audience=True):
    c = {'clientId': name, 'name': name, 'enabled': True, 'protocol': 'openid-connect',
         'publicClient': False, 'clientAuthenticatorType': 'client-secret',
         'secret': secret, 'standardFlowEnabled': False, 'implicitFlowEnabled': False,
         'directAccessGrantsEnabled': False, 'serviceAccountsEnabled': True,
         'fullScopeAllowed': False,
         'defaultClientScopes': [permission] if permission else [],
         'optionalClientScopes': []}
    if with_audience:
        c['protocolMappers'] = [audience_mapper()]
    return c


def realm_import():
    secrets_by_client = {k: secrets.token_urlsafe(40) for k in
                         ('deroyque-reader', 'deroyque-writer', 'deroyque-outsider', 'deroyque-wrong-aud', 'deroyque-other-issuer')}
    main = {'realm': REALM, 'enabled': True, 'sslRequired': 'none',
            'clientScopes': [scope('activation:read'), scope('activation:write')],
            'clients': [client('deroyque-reader', secrets_by_client['deroyque-reader'], 'activation:read'),
                        client('deroyque-writer', secrets_by_client['deroyque-writer'], 'activation:write'),
                        client('deroyque-outsider', secrets_by_client['deroyque-outsider']),
                        client('deroyque-wrong-aud', secrets_by_client['deroyque-wrong-aud'], 'activation:read', False)]}
    other = {'realm': OTHER_REALM, 'enabled': True, 'sslRequired': 'none',
             'clientScopes': [scope('activation:read')],
             'clients': [client('deroyque-other-issuer', secrets_by_client['deroyque-other-issuer'], 'activation:read')]}
    import_dir = DIR / 'realm-import'
    import_dir.mkdir()
    (import_dir / (REALM + '-realm.json')).write_text(json.dumps(main))
    (import_dir / (OTHER_REALM + '-realm.json')).write_text(json.dumps(other))
    return import_dir, secrets_by_client


def start_keycloak(port, import_dir):
    # All secrets are laboratory-only, generated per run, and never printed.
    admin_password = secrets.token_urlsafe(32)
    proc = subprocess.run(['docker', 'run', '-d', '--rm', '--name', CONTAINER,
        '-p', f'127.0.0.1:{port}:8080', '--memory', '1g',
        '-e', 'KC_BOOTSTRAP_ADMIN_USERNAME=ci-admin',
        '-e', 'KC_BOOTSTRAP_ADMIN_PASSWORD=' + admin_password,
        '-e', 'JAVA_OPTS_KC_HEAP=-XX:InitialRAMPercentage=15 -XX:MaxRAMPercentage=55',
        '-v', str(import_dir.resolve()) + ':/opt/keycloak/data/import:ro',
        args.keycloak_image, 'start-dev', '--import-realm'],
        capture_output=True, text=True, timeout=180)
    if proc.returncode:
        raise RuntimeError('Cannot start disposable Keycloak image (Docker failure, details not emitted)')


def token(base, realm, name, secret):
    url = base + '/realms/' + realm + '/protocol/openid-connect/token'
    body = urllib.parse.urlencode({'grant_type': 'client_credentials'}).encode()
    pair = base64.b64encode((name + ':' + secret).encode()).decode()
    code, raw, _ = request('POST', url, body,
        {'Authorization': 'Basic ' + pair, 'Content-Type': 'application/x-www-form-urlencoded'}, timeout=10)
    if code != 200:
        raise RuntimeError('Keycloak client_credentials exchange failed: HTTP ' + str(code))
    answer = json.loads(raw)
    if answer.get('token_type', '').lower() != 'bearer' or not answer.get('access_token'):
        raise RuntimeError('Keycloak token response lacks bearer access token')
    return answer['access_token']


def claims_unverified(token_value):
    # Inspect claims for expected test fixture setup, NOT as signature validation.
    segment = token_value.split('.')[1]
    return json.loads(base64.urlsafe_b64decode(segment + '=' * (-len(segment) % 4)))


def api(method, path, access_token=None, payload=None):
    h = {}
    if access_token is not None:
        h['Authorization'] = 'Bearer ' + access_token
    b = json.dumps(payload, separators=(',', ':')).encode() if payload is not None else None
    if b is not None:
        h['Content-Type'] = 'application/json'
    code, raw, resp_headers = request(method, f'http://127.0.0.1:{APPPORT}' + path, b, h)
    return code, raw.decode(errors='replace'), resp_headers


def run():
    global APPPORT
    jar = args.boot_jar.resolve()
    check(jar.is_file(), 'real Spring Boot executable archive exists')
    idp_port = freeport()
    base = f'http://127.0.0.1:{idp_port}'
    if args.use_local_keycloak:
        local_state = DIR / 'local-keycloak'
        proc = subprocess.run([sys.executable, str(ROOT/'scripts/local_keycloak.py'),
                 '--state-dir', str(local_state), 'up', '--port', str(idp_port),
                 '--image', args.keycloak_image, '--with-test-realm'],
                 capture_output=True, text=True, timeout=210)
        if proc.returncode:
            raise RuntimeError('First-class local Keycloak startup failed; private details suppressed')
        creds = json.loads((local_state/'credentials.json').read_text())['clients']
    else:
        import_dir, creds = realm_import()
        start_keycloak(idp_port, import_dir)
    discovery_path = '/realms/' + REALM + '/.well-known/openid-configuration'
    def discovery():
        code, body, _ = request('GET', base + discovery_path, timeout=3)
        return json.loads(body) if code == 200 else None
    metadata = eventually(discovery, 150)
    check(metadata is not None, 'genuine Keycloak OIDC discovery endpoint started')
    expected_issuer = base + '/realms/' + REALM
    check(metadata.get('issuer') == expected_issuer and
          metadata.get('token_endpoint') == expected_issuer + '/protocol/openid-connect/token' and
          metadata.get('jwks_uri') == expected_issuer + '/protocol/openid-connect/certs',
          'discovery issuer, JWKS and token endpoint are consistent')
    RESULT['issuer_verified'] = True
    keys_status, keys_body, _ = request('GET', metadata['jwks_uri'])
    check(keys_status == 200 and len(json.loads(keys_body).get('keys', [])) >= 1,
          'real OIDC issuer exposes at least one public signing key')
    reader = token(base, REALM, 'deroyque-reader', creds['deroyque-reader'])
    writer = token(base, REALM, 'deroyque-writer', creds['deroyque-writer'])
    outsider = token(base, REALM, 'deroyque-outsider', creds['deroyque-outsider'])
    bad_audience = token(base, REALM, 'deroyque-wrong-aud', creds['deroyque-wrong-aud'])
    bad_issuer = token(base, OTHER_REALM, 'deroyque-other-issuer', creds['deroyque-other-issuer'])
    read_claims, write_claims = claims_unverified(reader), claims_unverified(writer)
    check('activation:read' in read_claims.get('scope', '').split() and
          'activation:write' not in read_claims.get('scope', '').split() and
          AUDIENCE in read_claims.get('aud', []),
          'Keycloak reader token contains read scope and expected audience')
    check('activation:write' in write_claims.get('scope', '').split() and
          'activation:read' not in write_claims.get('scope', '').split() and
          AUDIENCE in write_claims.get('aud', []),
          'Keycloak writer token contains write scope and expected audience')
    check('activation:read' not in claims_unverified(outsider).get('scope', '').split() and
          'activation:write' not in claims_unverified(outsider).get('scope', '').split(),
          'Keycloak unrelated client has no activation scopes')
    check(AUDIENCE not in claims_unverified(bad_audience).get('aud', []),
          'Keycloak other-audience client does not contain Deroyque audience')
    check(claims_unverified(bad_issuer).get('iss') != expected_issuer,
          'Keycloak second realm has genuinely different OIDC issuer')
    sql('CREATE SCHEMA ' + SCHEMA)
    APPPORT = freeport()
    provider_port = freeport()
    jdbc = f'jdbc:postgresql://{DBHOST}:{DBPORT}/{DBNAME}?currentSchema={SCHEMA}'
    env = {'APP_AUTH_MODE': 'jwt', 'APP_JWT_ISSUER': expected_issuer,
           'APP_JWT_JWKS_URI': metadata['jwks_uri'], 'APP_JWT_AUDIENCE': AUDIENCE,
           'APP_JWT_ALLOW_HTTP_LOOPBACK': '1',
           'APP_API_ENABLED': '1', 'APP_LAB_METRICS_ENABLED': '0',
           'P13_JDBC': jdbc, 'P13_DB_USER': DBUSER, 'P13_DB_PASSWORD': DBPASS,
           'SPRING_DATASOURCE_URL': jdbc, 'SPRING_DATASOURCE_USERNAME': DBUSER,
           'SPRING_DATASOURCE_PASSWORD': DBPASS,
           'SPRING_FLYWAY_SCHEMAS': SCHEMA, 'SPRING_FLYWAY_DEFAULT_SCHEMA': SCHEMA,
           'P13_QUEUE': QUEUE, 'SERVER_PORT': str(APPPORT), 'P13_API_PORT': str(APPPORT),
           'SERVER_ADDRESS': '127.0.0.1',
           'P13_PROVIDER_URL': f'http://127.0.0.1:{provider_port}/activate',
           'P19_RECONCILE_URL': f'http://127.0.0.1:{provider_port}/lookup',
           'P16_PROVIDER_RETENTION_MS': '300000', 'P20_SIGNED_LOOKUP_REQUIRED': '1',
           'P20_PUBLIC_KEY_PEM': str(ROOT/'fixtures/keys/provider-test-public.pem'),
           'P20_EXPECTED_ISSUER': 'deroyque-lab-provider', 'P20_EXPECTED_KID': 'lab-key-01'}
    provider_env = {'P19_PROVIDER_SQLITE': str(DIR/'provider.sqlite3'),
                    'P19_PROVIDER_PORT': str(provider_port),
                    'P19_PROVIDER_RETENTION_MS': '600000',
                    'P20_PRIVATE_KEY_PEM': str(ROOT/'fixtures/keys/provider-test-private.pem'),
                    'P20_ISSUER': 'deroyque-lab-provider', 'P20_KID': 'lab-key-01'}
    prov = spawn('provider', [sys.executable, '-u', str(ROOT/'provider/independent_provider.py')], provider_env)
    def provider_ready():
        if prov.poll() is not None:
            raise RuntimeError('independent provider exited unexpectedly')
        code, _, _ = request('GET', f'http://127.0.0.1:{provider_port}/health', timeout=2)
        return code == 200
    check(bool(eventually(provider_ready,20)), 'independent SQLite-backed provider ready')
    app = spawn('spring-keycloak', ['java','-Xmx384m','-jar', str(jar)], env)
    def spring_ready():
        if app.poll() is not None:
            raise RuntimeError('Spring Boot resource server unexpectedly stopped')
        return api('GET', '/api/v1/health/readiness')[0] == 200
    check(bool(eventually(spring_ready,65)), 'Spring Boot JWT resource server ready with real Keycloak issuer')
    history = sql('SELECT version,success FROM ' + SCHEMA + '.flyway_schema_history ORDER BY installed_rank')
    check('1|t' in history and '2|t' in history, 'Flyway V1/V2 applied in isolated schema')
    unknown = '/api/v1/requests/' + str(uuid.uuid4())
    check(api('GET', unknown)[0] == 401, 'no JWT rejected with HTTP 401')
    check(api('GET', unknown, reader)[0] == 404, 'genuine read-scope Keycloak token reaches read API')
    check(api('POST', '/api/v1/requests', reader, {})[0] == 403, 'Keycloak reader cannot write HTTP 403')
    check(api('GET', unknown, writer)[0] == 403, 'Keycloak writer cannot read HTTP 403')
    check(api('GET', unknown, outsider)[0] == 403, 'unrelated Keycloak client denied HTTP 403')
    check(api('GET', unknown, bad_audience)[0] == 401, 'authentic Keycloak JWT with wrong audience rejected HTTP 401')
    check(api('GET', unknown, bad_issuer)[0] == 401, 'authentic JWT from distinct Keycloak realm rejected HTTP 401')
    payload = {'externalId': EXTERNAL_ID, 'customerId': 'keycloak-ci-customer', 'productCode': 'basic'}
    status, body, headers = api('POST', '/api/v1/requests', writer, payload)
    request_id = json.loads(body).get('id') if status == 202 else None
    check(status == 202 and isinstance(request_id,str) and len(request_id) == 36,
          'Keycloak writer creates durable request via HTTP 202')
    check(headers.get('Location') == '/api/v1/requests/' + request_id,
          'created resource Location points at durable UUID')
    status, body, _ = api('POST', '/api/v1/requests', writer, payload)
    check(status == 200 and json.loads(body).get('id') == request_id,
          'Keycloak writer replay preserves UUID HTTP 200')
    check(api('POST', '/api/v1/requests', writer, {**payload,'productCode':'different'})[0] == 409,
          'Keycloak write-scope conflict correctly returns HTTP 409')
    def finished():
        status, raw, _ = api('GET', '/api/v1/requests/' + request_id, reader)
        return status == 200 and json.loads(raw).get('status') == 'DONE'
    check(bool(eventually(finished,60)), 'activation completed with real Keycloak read/write tokens')
    with sqlite3.connect(DIR/'provider.sqlite3', timeout=10) as db:
        effects = db.execute('SELECT count(*) FROM effect WHERE key=?', (EXTERNAL_ID,)).fetchone()[0]
    check(effects == 1, 'independent SQLite provider committed exactly one external effect')
    check(sql('SELECT count(*) FROM ' + SCHEMA + '.p20_signed_attestation') == '1',
          'provider signed evidence is durably stored in PostgreSQL')
    RESULT['signed_provider_effect_verified'] = True
    print('KEYCLOAK_OIDC_PASS provider='+('local-option' if args.use_local_keycloak else 'test-internal')+
          ' client_credentials=true external_effects=1',flush=True)
    return 0


def main():
    code = 1
    try:
        code = run()
    except Exception as e:
        RESULT['error_type'] = type(e).__name__
        # Never echo secrets, tokens, or logs in CI output.
        print('KEYCLOAK_OIDC_ERROR ' + type(e).__name__ + ' (private diagnostic logs suppressed)', flush=True)
    finally:
        for proc, file in reversed(CHILDREN):
            if proc.poll() is None:
                try:os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError:pass
                try:proc.wait(timeout=6)
                except subprocess.TimeoutExpired:
                    try:os.killpg(proc.pid,signal.SIGKILL)
                    except ProcessLookupError:pass
                    proc.wait()
            file.close()
        try:
            if args.use_local_keycloak:
                subprocess.run([sys.executable, str(ROOT/'scripts/local_keycloak.py'),
                   '--state-dir', str(DIR/'local-keycloak'), 'down'],capture_output=True,timeout=45)
            else:
                subprocess.run(['docker','rm','-f',CONTAINER],capture_output=True,timeout=35)
        except (OSError,subprocess.TimeoutExpired):
            print('KEYCLOAK_OIDC_CLEANUP could not confirm docker removal',flush=True)
        failures=sum(not x['passed'] for x in RESULT['checks'])
        passes=len(RESULT['checks'])-failures
        RESULT.update({'failed': failures, 'passed': passes, 'total': len(RESULT['checks']), 'exit_code': code})
        print(f'KEYCLOAK_OIDC_RESULT FAILURES {failures}/{len(RESULT["checks"])} PASSED {passes}/{len(RESULT["checks"])}',flush=True)
        if args.output:
            args.output.parent.mkdir(parents=True,exist_ok=True)
            args.output.write_text(json.dumps(RESULT, indent=2)+'\n')
        TMP.cleanup()
    return code

if __name__ == '__main__': sys.exit(main())
