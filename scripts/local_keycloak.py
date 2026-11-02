#!/usr/bin/env python3
"""Optional LOCAL Keycloak OIDC provider for Deroyque development/demo only.

Standard library + Docker CLI. The credentials and realm import are generated
under .local/ (gitignored), permission 0700/0600, and never checked into Git.
This does not configure a production identity provider or production TLS.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import secrets
import shlex
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATE = ROOT / '.local' / 'keycloak'
IMAGE = 'quay.io/keycloak/keycloak:26.8.1'
REALM = 'deroyque-local'
OTHER_REALM = 'deroyque-local-untrusted'
AUDIENCE = 'deroyque-activation-api'
LABEL = 'org.deroyque.local-keycloak'
CLIENTS = ('deroyque-reader', 'deroyque-writer', 'deroyque-outsider', 'deroyque-wrong-aud')


def scope(name):
    return {'name': name, 'protocol': 'openid-connect',
            'attributes': {'include.in.token.scope': 'true', 'display.on.consent.screen': 'false'}}


def client(name, credential, permission=None, audience=True):
    c = {'clientId': name, 'enabled': True, 'protocol': 'openid-connect',
         'publicClient': False, 'clientAuthenticatorType': 'client-secret',
         'secret': credential, 'standardFlowEnabled': False,
         'implicitFlowEnabled': False, 'directAccessGrantsEnabled': False,
         'serviceAccountsEnabled': True, 'fullScopeAllowed': False,
         'defaultClientScopes': [permission] if permission else [], 'optionalClientScopes': []}
    if audience:
        c['protocolMappers'] = [{'name': 'deroyque-api-audience',
              'protocol': 'openid-connect', 'protocolMapper': 'oidc-audience-mapper',
              'consentRequired': False,
              'config': {'included.custom.audience': AUDIENCE,
                         'id.token.claim': 'false', 'access.token.claim': 'true'}}]
    return c


def realm_documents(credentials, with_test_realm=False):
    main = {'realm': REALM, 'enabled': True, 'sslRequired': 'none',
            'clientScopes': [scope('activation:read'), scope('activation:write')],
            'clients': [client('deroyque-reader', credentials['deroyque-reader'], 'activation:read'),
                        client('deroyque-writer', credentials['deroyque-writer'], 'activation:write'),
                        client('deroyque-outsider', credentials['deroyque-outsider']),
                        client('deroyque-wrong-aud', credentials['deroyque-wrong-aud'],
                               'activation:read', audience=False)]}
    documents = {REALM: main}
    if with_test_realm:
        documents[OTHER_REALM] = {'realm': OTHER_REALM, 'enabled': True, 'sslRequired': 'none',
           'clientScopes': [scope('activation:read')],
           'clients': [client('deroyque-other-issuer', credentials['deroyque-other-issuer'], 'activation:read')]}
    return documents


def write_private(path, content):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        stream.write(content)


def read_state(folder):
    file = folder / 'state.json'
    if not file.is_file():
        raise RuntimeError('No local Keycloak state. Run: python3 scripts/local_keycloak.py up')
    d = json.loads(file.read_text(encoding='utf-8'))
    if not isinstance(d, dict) or d.get('realm') != REALM or not str(d.get('container', '')).startswith('deroyque-local-'):
        raise RuntimeError('Invalid local Keycloak state; refusing to control unspecified Docker container')
    return d


def docker(*args, timeout=30, **kwargs):
    return subprocess.run(['docker', *args], capture_output=True, text=True, timeout=timeout, **kwargs)


def valid_owned_container(name):
    r = docker('inspect', '-f', '{{ index .Config.Labels "' + LABEL + '" }}', name, timeout=10)
    return r.returncode == 0 and r.stdout.strip() == 'true'


def discovery(base):
    try:
        with urllib.request.urlopen(base + '/realms/' + REALM + '/.well-known/openid-configuration', timeout=3) as r:
            if r.status == 200:
                return json.load(r)
    except (OSError, ValueError, urllib.error.URLError):
        pass
    return None


def ensure_local_issuer(base):
    meta = discovery(base)
    issuer = base + '/realms/' + REALM
    if not meta or meta.get('issuer') != issuer or meta.get('jwks_uri') != issuer + '/protocol/openid-connect/certs' or meta.get('token_endpoint') != issuer + '/protocol/openid-connect/token':
        raise RuntimeError('Keycloak OIDC discovery/issuer does not match expected local configuration')
    return meta


def up(folder, port, image, test_realm=False, timeout_seconds=150):
    if not 1024 <= port <= 65535:
        raise ValueError('Port must be between 1024 and 65535')
    if (folder / 'state.json').exists():
        existing = read_state(folder)
        if existing['port'] != port or existing['image'] != image or bool(existing.get('test_realm')) != bool(test_realm):
            raise RuntimeError('Existing local Keycloak configuration differs; use down first')
        if not valid_owned_container(existing['container']):
            raise RuntimeError('Stale local Keycloak state; use down before starting another instance')
        ensure_local_issuer(existing['base'])
        return existing
    if not image.startswith(('quay.io/keycloak/keycloak:',)):
        raise ValueError('Only an explicitly versioned quay.io/keycloak/keycloak image is accepted')
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    folder.chmod(0o700)
    imported = folder / 'realm-import'
    # The Keycloak container is not the host UID; bind-mounted realm imports must
    # be readable by its non-root runtime. Parent folder remains 0700 and gitignored.
    imported.mkdir(exist_ok=True, mode=0o755)
    imported.chmod(0o755)
    names = list(CLIENTS) + (['deroyque-other-issuer'] if test_realm else [])
    credentials = {name: secrets.token_urlsafe(40) for name in names}
    for realm, document in realm_documents(credentials, test_realm).items():
        realm_file = imported / f'{realm}-realm.json'
        write_private(realm_file, json.dumps(document, indent=2) + '\n')
        realm_file.chmod(0o644)  # container's non-root Keycloak UID must read it
    # Random per-run root password; no default credentials, no committed secret.
    root_password = secrets.token_urlsafe(40)
    name = 'deroyque-local-' + secrets.token_hex(6)
    base = f'http://127.0.0.1:{port}'
    env = {**os.environ, 'KC_BOOTSTRAP_ADMIN_PASSWORD': root_password}
    command = ['run', '-d', '--name', name, '--label', LABEL + '=true',
               '--publish', f'127.0.0.1:{port}:8080', '--memory', '1g',
               '--env', 'KC_BOOTSTRAP_ADMIN_USERNAME=local-admin',
               '--env', 'KC_BOOTSTRAP_ADMIN_PASSWORD',
               '--env', 'JAVA_OPTS_KC_HEAP=-XX:InitialRAMPercentage=15 -XX:MaxRAMPercentage=55',
               '--volume', str(imported.resolve()) + ':/opt/keycloak/data/import:ro',
               image, 'start-dev', '--import-realm']
    started = docker(*command, env=env, timeout=180)
    if started.returncode:
        raise RuntimeError('Docker could not start local Keycloak (ensure Docker is running and port is free)')
    try:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            try:
                ensure_local_issuer(base)
                break
            except RuntimeError:
                time.sleep(.6)
        else:
            raise RuntimeError('Local Keycloak did not become OIDC-ready before deadline')
        state = {'realm': REALM, 'test_realm': bool(test_realm), 'port': port,
                 'base': base, 'container': name, 'image': image, 'audience': AUDIENCE}
        write_private(folder / 'credentials.json', json.dumps({'clients': credentials}, indent=2) + '\n')
        write_private(folder / 'state.json', json.dumps(state, indent=2) + '\n')
        return state
    except BaseException:
        docker('rm', '-f', name, timeout=40)
        raise


def down(folder):
    statefile = folder / 'state.json'
    if statefile.exists():
        state = read_state(folder)
        if valid_owned_container(state['container']):
            r = docker('rm', '-f', state['container'], timeout=40)
            if r.returncode:
                raise RuntimeError('Failed to remove the owned Keycloak container')
        elif docker('inspect', state['container'], timeout=10).returncode == 0:
            raise RuntimeError('Container exists but lacks ownership label; refusing to remove it')
    for f in (folder / 'state.json', folder / 'credentials.json'):
        if f.is_file():
            f.unlink()
    imported = folder / 'realm-import'
    if imported.is_dir():
        for f in imported.glob('*-realm.json'):
            if f.name in (REALM + '-realm.json', OTHER_REALM + '-realm.json'):
                f.unlink()
        if not list(imported.iterdir()):
            imported.rmdir()
    if folder.exists() and not list(folder.iterdir()):
        folder.rmdir()


def token(folder, role):
    state = read_state(folder)
    ensure_local_issuer(state['base'])
    name = {'reader':'deroyque-reader', 'writer':'deroyque-writer',
            'outsider':'deroyque-outsider'}.get(role)
    if not name:
        raise ValueError('Unknown client role')
    secrets_data = json.loads((folder / 'credentials.json').read_text())['clients']
    pair = base64.b64encode((name + ':' + secrets_data[name]).encode()).decode()
    body = urllib.parse.urlencode({'grant_type':'client_credentials'}).encode()
    req = urllib.request.Request(state['base']+'/realms/'+REALM+'/protocol/openid-connect/token',
            data=body, headers={'Authorization':'Basic '+pair,
                    'Content-Type':'application/x-www-form-urlencoded'}, method='POST')
    with urllib.request.urlopen(req, timeout=10) as resp:
        answer = json.load(resp)
    if answer.get('token_type', '').lower() != 'bearer' or not answer.get('access_token'):
        raise RuntimeError('Local Keycloak did not issue a bearer access token')
    return answer['access_token']


def validate_https_endpoint(url, label):
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError(label + ' must be a valid HTTPS URL')
    return url


def configuration(provider, folder=None, issuer=None, jwks_uri=None, audience=None):
    if provider == 'local':
        state = read_state(folder)
        meta = ensure_local_issuer(state['base'])
        return {'APP_AUTH_MODE': 'jwt', 'APP_JWT_ISSUER': meta['issuer'],
                'APP_JWT_JWKS_URI': meta['jwks_uri'],
                'APP_JWT_AUDIENCE': state['audience'], 'APP_JWT_ALLOW_HTTP_LOOPBACK': '1'}
    if provider == 'external':
        if not issuer or not jwks_uri or not audience or not audience.strip():
            raise ValueError('External provider requires --issuer, --jwks-uri and --audience')
        return {'APP_AUTH_MODE': 'jwt',
                'APP_JWT_ISSUER': validate_https_endpoint(issuer, '--issuer'),
                'APP_JWT_JWKS_URI': validate_https_endpoint(jwks_uri, '--jwks-uri'),
                'APP_JWT_AUDIENCE': audience.strip()}
    raise ValueError('Unknown OIDC provider mode')


def shell_exports(values):
    lines = ['unset APP_JWT_ALLOW_HTTP_LOOPBACK']
    return '\n'.join(lines+[f'export {name}={shlex.quote(value)}' for name,value in values.items()]) + '\n'


def cli(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--state-dir', type=Path, default=DEFAULT_STATE)
    sub = p.add_subparsers(dest='command', required=True)
    a = sub.add_parser('up', help='Start local loopback Keycloak with generated demo clients')
    a.add_argument('--port', type=int, default=18090)
    a.add_argument('--image', default=IMAGE)
    a.add_argument('--with-test-realm', action='store_true', help='Additional realm for CI negative-issuer tests')
    sub.add_parser('status')
    sub.add_parser('down')
    a = sub.add_parser('token', help='Print a short-lived client_credentials token to stdout: handle as a secret')
    a.add_argument('--client', choices=('reader','writer','outsider'), required=True)
    a = sub.add_parser('env', help='Print shell exports to select local or external OIDC issuer')
    a.add_argument('--provider', choices=('local','external'), default='local')
    a.add_argument('--issuer')
    a.add_argument('--jwks-uri')
    a.add_argument('--audience')
    opts = p.parse_args(argv)
    folder = opts.state_dir.expanduser().resolve()
    if opts.command == 'up':
        s = up(folder,opts.port,opts.image,opts.with_test_realm)
        print('LOCAL_KEYCLOAK_READY issuer='+s['base']+'/realms/'+REALM+' (development only)')
        print('Use: eval "$(python3 scripts/local_keycloak.py env)"')
    elif opts.command == 'status':
        s = read_state(folder)
        ensure_local_issuer(s['base'])
        print('LOCAL_KEYCLOAK_READY issuer='+s['base']+'/realms/'+REALM)
    elif opts.command == 'down':
        down(folder)
        print('LOCAL_KEYCLOAK_STOPPED')
    elif opts.command == 'token':
        print(token(folder,opts.client))
    elif opts.command == 'env':
        print(shell_exports(configuration(opts.provider, folder, opts.issuer, opts.jwks_uri, opts.audience)),end='')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(cli())
    except (RuntimeError, ValueError, FileNotFoundError, urllib.error.URLError, subprocess.TimeoutExpired) as e:
        print('LOCAL_KEYCLOAK_ERROR ' + str(e), file=sys.stderr)
        sys.exit(1)
