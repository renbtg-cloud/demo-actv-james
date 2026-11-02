# Milestone 18 — OAuth2/JWT security boundary

## Intent and threat model

The service previously accepted only static bearer tokens. The Spring deployment now defaults to JWT mode, using Spring Security's OAuth2 resource-server chain. Never construe this as an authorization server, user-management system, or production-grade identity platform. TLS termination, key custody, authorization issuer operations, secrets management and perimeter hardening remain deployment concerns.

## Behavior

- `APP_AUTH_MODE=jwt` is the default. Required: `APP_JWT_ISSUER`, `APP_JWT_JWKS_URI`, `APP_JWT_AUDIENCE`. All are validated at initialization. JWKS and issuer must use HTTPS, except explicit CI-only HTTP loopback override.
- RS256 signatures are verified against the configured JWKS; issuer, audience, `exp`, `nbf` are checked. Credentials never come from request-supplied key URLs.
- `POST /api/v1/requests` requires `activation:write`; `GET /api/v1/requests/{id}` requires `activation:read`. Anonymous calls return 401, valid JWTs lacking the required scope return 403.
- Liveness/readiness remain publicly callable. Other paths are denied by default, including lab ingress metrics.
- `APP_AUTH_MODE=lab` explicitly opts into the old distinct opaque read/write bearer tokens, preserving the legacy experimental controls; no JWT fallback or mixed authentication is permitted.
- Application core remains framework-neutral, with a security callback that reads **validated** `JwtAuthenticationToken` authorities from Spring Security's context, never the raw Authorization header.

## Real integration evidence

The seventh GitHub Actions matrix job `jwt-security` builds the actual Spring executable JAR and starts it with PostgreSQL 17, RabbitMQ 4.4 and an independent SQLite provider. It starts a disposable RSA keypair / loopback JWKS server, issues deliberately valid and invalid tokens, checks HTTP 401/403, correct scopes, idempotency, signed evidence and one durable external effect. No pre-generated signing keys or raw tokens are committed.

Runtime CI results must be independently verified before claiming this milestone fully complete. Previous six jobs must remain green.
