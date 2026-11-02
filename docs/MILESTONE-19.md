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

## Verified acceptance evidence

On 2 November 2026, GitHub Actions [run 41469917139](https://github.com/renbtg-cloud/demo-actv-dryqe/actions/runs/41469917139), commit `ebf145d293ffffbe9edb75b91b98d1f029118cee`, completed **eight real Spring Boot jobs with 0/238 failed checks; 238/238 passed**:

| Scenario | Failures / total | Passed / total |
|---|---:|---:|
| Genuine Keycloak OIDC `client_credentials` | 0/26 | 26/26 |
| Locally signed JWT rejection and scope acceptance | 0/23 | 23/23 |
| Four-JVM contention for the same PostgreSQL row | 0/22 | 22/22 |
| Exact crash windows | 0/44 | 44/44 |
| Three competing Spring recovery JVMs | 0/44 | 44/44 |
| PostgreSQL / RabbitMQ interruptions | 0/19 | 19/19 |
| Late and contradictory signed evidence | 0/16 | 16/16 |
| Four Spring workers, two `SIGKILL`s, 24 producer JVMs | 0/44 | 44/44 |
| **Total** | **0/238** | **238/238** |

The Keycloak test exercised OIDC discovery, actual service-account token issuance for separate clients, wrong audience and cross-realm rejection, HTTP 202/200/409 semantics, `DONE` after asynchronous processing, one independent external SQLite effect and stored signed provider proof. The high-load regression completed 960 HTTP POSTs across 24 overlapping producer JVMs, 160 unique keys and 160 effects.

These tests demonstrate the specified behavior in isolated CI—not a production-grade identity-provider deployment, formal security verification or universal exactly-once guarantee.
