"""No-Docker unit checks for optional local OIDC distribution's safety contract."""
import importlib.util
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('local_keycloak', ROOT/'scripts/local_keycloak.py')
local = importlib.util.module_from_spec(spec)
spec.loader.exec_module(local)


class LocalKeycloakConfigTest(unittest.TestCase):
    def test_realm_m2m_scopes_audience_and_no_password_flow(self):
        creds = {c: 'generated-'+c for c in (*local.CLIENTS, 'deroyque-other-issuer')}
        realms = local.realm_documents(creds, True)
        self.assertEqual(set(realms), {local.REALM, local.OTHER_REALM})
        clients = {c['clientId']: c for c in realms[local.REALM]['clients']}
        self.assertEqual(clients['deroyque-reader']['defaultClientScopes'], ['activation:read'])
        self.assertEqual(clients['deroyque-writer']['defaultClientScopes'], ['activation:write'])
        self.assertEqual(clients['deroyque-outsider']['defaultClientScopes'], [])
        self.assertEqual(clients['deroyque-wrong-aud']['protocolMappers'] if 'protocolMappers' in clients['deroyque-wrong-aud'] else [], [])
        for c in clients.values():
            self.assertTrue(c['serviceAccountsEnabled'])
            self.assertFalse(c['directAccessGrantsEnabled'])
            self.assertFalse(c['standardFlowEnabled'])
            self.assertFalse(c['fullScopeAllowed'])
        self.assertEqual(len(local.realm_documents(creds)), 1)
        self.assertEqual(clients['deroyque-reader']['protocolMappers'][0]['config']['included.custom.audience'], local.AUDIENCE)

    def test_external_env_https_only_and_explicit_scope(self):
        with self.assertRaises(ValueError):
            local.configuration('external', issuer='http://remote.org/realms/idp',
                jwks_uri='https://remote.org/certs', audience='deroyque-activation-api')
        with self.assertRaises(ValueError):
            local.configuration('external', issuer='https://remote.org',
                jwks_uri='https://remote.org/certs')
        env = local.configuration('external', issuer='https://login.example/realms/prod',
                jwks_uri='https://login.example/realms/prod/protocol/openid-connect/certs',
                audience='activation-api')
        script = local.shell_exports(env)
        self.assertIn('unset APP_JWT_ALLOW_HTTP_LOOPBACK', script)
        self.assertNotIn('export APP_JWT_ALLOW_HTTP_LOOPBACK=1', script)
        self.assertEqual(env['APP_AUTH_MODE'], 'jwt')
        with self.assertRaises(ValueError):
            local.configuration('external', issuer='https://user:pass@example.com/realm',
                jwks_uri='https://example.com/keys', audience='a')

    def test_private_state_and_docker_ports_are_loopback_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)/'private'
            commands = []
            def fake_docker(*args, **kwargs):
                commands.append((args, kwargs))
                if args[0] == 'run':
                    return type('P', (), {'returncode':0,'stdout':'fake-id'})()
                return type('P', (), {'returncode':0,'stdout':'true'})()
            def discovery(base):
                issuer = base + '/realms/' + local.REALM
                return {'issuer':issuer, 'jwks_uri':issuer+'/protocol/openid-connect/certs',
                        'token_endpoint':issuer+'/protocol/openid-connect/token'}
            with patch.object(local, 'docker', side_effect=fake_docker), patch.object(local,'discovery',side_effect=discovery):
                state = local.up(folder, 18090, local.IMAGE)
                self.assertEqual(state['port'], 18090)
                self.assertFalse(state['test_realm'])
                self.assertTrue(any('127.0.0.1:18090:8080' in x for x in commands[0][0]))
                self.assertEqual(stat.S_IMODE(folder.stat().st_mode),0o700)
                self.assertEqual(stat.S_IMODE((folder/'credentials.json').stat().st_mode),0o600)
                self.assertEqual(stat.S_IMODE((folder/'state.json').stat().st_mode),0o600)
                cfg = local.configuration('local', folder)
                self.assertEqual(cfg['APP_JWT_ALLOW_HTTP_LOOPBACK'],'1')
                with self.assertRaises(RuntimeError):
                    local.up(folder, 18091, local.IMAGE)
                local.down(folder)
                self.assertFalse((folder/'credentials.json').exists())
                self.assertFalse((folder/'state.json').exists())
                self.assertEqual(commands[-1][0][0:2], ('rm','-f'))

    def test_no_static_credentials_and_port_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                local.up(Path(tmp), 80, local.IMAGE)
            with self.assertRaises(ValueError):
                local.up(Path(tmp), 70000, local.IMAGE)
            with self.assertRaises(ValueError):
                local.up(Path(tmp), 18090, 'keycloak:latest')


if __name__ == '__main__':
    unittest.main()
