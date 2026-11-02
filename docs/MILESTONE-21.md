# Milestone 21 — Dynamic JWKS lifecycle and fail-closed issuer outage

## Motivation and scope

Milestones 18–20 established real Spring JWT signature/audience/issuer/scope checks,
and genuine Keycloak `client_credentials`, both against local and external OIDC
provisioning. A fixed test JWKS does **not** exercise the resource server while
keys are being introduced or the JWKS endpoint is temporarily unavailable.
This milestone strengthens acceptance evidence without modifying durable admission,
outbox, RabbitMQ workers or provider processing.

## New adversarial checks

- A disposable `127.0.0.1` HTTP JWKS fixture creates fresh RSA-2048 private
  signing keys per test, each with a **distinct** `kid`. The server only publishes
  public JWK fields and can atomically add/remove published keys and return 503.
- The existing real Spring Security acceptance first warms the decoder using
  an initial key. It then adds another key while both are valid, signs a token
  with the new `kid`, and requires authenticated 404 application lookup without
  restarting the Spring JVM. It checks that another JWKS GET happened.
- During HTTP 503 from JWKS, a token signed under a **previously unpublished**
  key must fail with HTTP 401; the test does not accept a 2xx, 403 or 5xx.
  After restoring the endpoint and publishing the key, that same token must
  become usable, within a bounded retry window, without restarting Spring.
- The fixture then retires the initial key from its published set, **records**
  the old token's immediate observed HTTP status, but does not require instant
  rejection. Nimbus/Spring may cache public keys; removing a JWK is not a
  real-time revocation mechanism.
- 24 real concurrent GET requests use two currently published signing keys;
  additional rotated-key tokens with an incorrect `iss` or `aud` must return 401.
- Four standalone `unittest` cases check generated RS256 signature verification,
  publication/retirement, 503/recovery, and parallel JWKS snapshots without
  Docker or a running Spring app.

## Security/nonclaims

The signing keys, HTTP JWKS server, and all bearer tokens are **CI-only**; the
fixture is not an authorization server or a replacement for Keycloak. In particular:

1. No global or immediate logout/revocation follows from removing a JWK.
2. A JWKS endpoint outage should deny *unrecognized* keys, not implicitly
   revoke previously cached valid keys.
3. Test exercise is bounded; it does not prove behavior under prolonged IdP
   outages, large cache populations, clock-skew attacks, or production TLS.
4. The benchmark/activation engine's throughput and exactly-once claims do not
   change; the new requests are security acceptance probes, not a load benchmark.

## Verification

Local preflight:

```sh
python3 -m unittest discover -s tests -p test_jwks_rotation_fixture.py -v
python3 -m py_compile tests/jwks_rotation_fixture.py tests/run_spring_jwt_security.py
```

The existing `jwt-security` job in `.github/workflows/spring-real-integration.yml`
performs the above unit cases and then runs the **real Spring Boot + PostgreSQL 17
+ RabbitMQ 4.4 + external SQLite provider** lifecycle checks; all other
previously green matrix jobs remain enabled. **Do not claim Milestone 21
integration passed until that GitHub run finishes successfully.** Preserve the
exact failed/total and passed/total counts from the execution.
