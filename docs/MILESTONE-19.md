# Milestone 19 — Real OIDC issuer / Keycloak client credentials

## Purpose

Milestone 18 proved Spring Security's JWT validation against a local test-only signer and JWKS server. Milestone 19 adds a **genuine independent OpenID Connect authorization server** to the acceptance suite; it does not modify the already tested durable activation engine.

A dedicated GitHub Actions matrix job launches a disposable **Keycloak** development-mode container, bootstraps two isolated realms, and obtains **real service-account OAuth2 access tokens** using the `client_credentials` grant and Keycloak's token endpoint. Those tokens are presented to the actual Spring Boot REST API. The runtime uses the issuer and JWKS URI discovered via Keycloak's OIDC discovery document.

Authorization cases include separate machine clients for `activation:read`, `activation:write`, no activation scope, wrong API audience, and a token issued by a different realm. Keycloak authenticates each client; Spring independently validates signature/JWKS, issuer, audience, token validity and granted scopes.

The test also exercises HTTP 202 creation, 200 idempotent replay, 409 conflicting payload, eventual `DONE`, **one effect in the independent SQLite provider**, and durable signed evidence in PostgreSQL. Flyway starts from a fresh temporary schema; the RabbitMQ queue is isolated per test.

## Run

The GitHub workflow `Spring Boot real integration` invokes:

```bash
mvn -B -ntp -f adapter-springboot/pom.xml clean package
python3 -u tests/run_spring_keycloak_oidc.py --boot-jar adapter-springboot/target/deroyque-springboot-0.10.0-SNAPSHOT.jar
```

Requires Docker with Keycloak image access, Java 21, Maven, PostgreSQL 17, RabbitMQ 4.4, Python, `psql` and the `cryptography` dependency for the separate provider. The Keycloak container is removed after the run. Keycloak realms and client credentials are **generated fresh** and retained only inside temporary directories, not committed or printed.

## Security boundaries

- Keycloak uses `start-dev`, HTTP bound to `127.0.0.1`, and a development database **only inside CI**. Never use those settings for an internet-facing provider.
- The Spring test sets `APP_JWT_ALLOW_HTTP_LOOPBACK=1` for the disposable issuer. Production still requires HTTPS for issuer and JWKS URI; use TLS and an externally administered IdP.
- Client credentials authorization proves **machine-to-machine** OAuth2, not interactive user authorization or account lifecycle management.
- Key rotation, token revocation, identity-provider downtime, multi-tenant authorization and operational secret management remain separate unvalidated concerns.
- The signed provider evidence uses separate test-only credentials; its authenticity is distinct from the OAuth2 client token.

**Acceptance status**: evaluate the `oidc-keycloak` CI job and record its exact verified result; a passing Maven build alone is not sufficient.
