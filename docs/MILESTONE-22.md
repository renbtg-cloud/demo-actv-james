# Milestone 22 — Access-token lifetime profile and adversarial JOSE headers

## Purpose

Milestone 21 confirmed Spring Security/Nimbus can dynamically retrieve new
trusted signing keys and fail closed on unrecognized keys while the configured
JWKS issuer is unavailable. The next risk is **a valid-looking JWT with an
unacceptable security profile** or an attacker-chosen key/algorithm source.
This milestone tests those cases on the real Spring Boot REST listener, without
changing request admission, RabbitMQ worker, PostgreSQL ledger, or the mock
provider's idempotency engine.

## Explicit, enforced JWT time profile

In `APP_AUTH_MODE=jwt`, the Spring `JwtDecoder` now requires:

- Nonblank `sub` (subject identity) and present `iat` / `exp` timestamps.
- `exp > iat`, and `exp - iat` at most `APP_JWT_MAX_LIFETIME_SECONDS`.
- `iat` not more than 60 seconds in the future relative to the server clock.
- If `nbf` is supplied, it must not be later than `exp`.

The max lifetime defaults to **3600 seconds** and allows integer values from
**60 to 86400 seconds** inclusive. Invalid limits fail initialization.
Existing `JwtValidators.createDefaultWithIssuer` (issuer, `exp`, `nbf` with
standard skew) plus an independent resource-server audience validator still
apply. Scope authorization is unchanged. The new limit is a compatibility
choice: an operator must configure the issuer's *access token* lifetime
accordingly; refresh-token TTLs are irrelevant. Clock-skew tolerance is not
an alternative to synchronizing service and issuer clocks.

## Actual attack cases

A disposable RSA-2048 fixture creates synthetic JWTs without using production
secrets. The acceptance harness asserts precisely HTTP **401**, never 403,
2xx or 5xx, for:

1. `alg=none` unsigned compact token;
2. `HS256` HMAC whose secret is derived entirely from published RSA JWK data;
3. a **cryptographically valid** RS512 token signed by the trusted RSA key,
   but not an algorithm allowed by the decoder's RS256 configuration;
4. a forged RSA JWT whose JOSE `jku` references a separate attacker-controlled
   HTTP endpoint; additionally, that endpoint's GET counter must remain zero;
5. a forged RSA JWT embedding an untrusted public `jwk` in its header;
6. missing `exp`, missing `iat`, missing `sub`;
7. lifetime longer than the default maximum;
8. `iat` too far in the future;
9. zero/negative lifetime.

A valid read-scope JWT with a small positive issuance skew must still reach the
application and return 404 for a random unknown request. The original JWT suite
continues checking expiry, `nbf`, issuer/audience, wrong scope, durable
activation, conflict/replay, signed evidence, JWKS rotation and outages.

The standalone test `tests/test_jwt_adversarial_vectors.py` validates the
attack fixture's actual token formats, HMAC calculation and RS512 signature.
**Those unit tests do not establish that Spring rejects the attacks**; only
`tests/run_spring_jwt_security.py` on a real Spring Boot process does.

## Verification and candid limitations

Local, no external services:

```sh
python3 -m unittest discover -s tests -p test_jwks_rotation_fixture.py -v
python3 -m unittest discover -s tests -p test_jwt_adversarial_vectors.py -v
python3 -m py_compile tests/run_spring_jwt_security.py
```

The `jwt-security` GitHub Actions job performs these tests and starts real
Spring Boot with a fresh Flyway PostgreSQL schema, RabbitMQ and independent
SQLite provider. The other eight real integration jobs, including two Keycloak
variants, remain enabled. **Do not describe new Milestone 22 integration tests
as passing until their workflow run finishes successfully.**

Not demonstrated: cross-tenant access authorization, OAuth2 token revocation
or introspection, immediate invalidation of cached JWKS keys, operational
secrets rotation, TLS distribution, identity-provider HA, production rate
limits, and resistance to every implementation-specific JOSE attack. This
suite addresses distinct falsifiable cases, not a broad security certification.
