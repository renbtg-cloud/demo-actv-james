# Deroyque / demoactv — experimental distributed activation engine

> **Asynchronous fault-tolerant product activation**

This repository contains the **actual experimental source code**, not the older documentation-only preview. The three primary deliverables are the asynchronous activation service, an independent HTTP provider simulator, and a configurable multi-JVM incoming-transaction simulator. They are developed as one evolving system.

**Verified baseline (2 November 2026, Milestone 22):** all nine real Spring
integration jobs succeeded; the JWT security job reported **0/44 failed,
44/44 passed**, including RSA rotation, outage and JOSE attack probes. See
[GitHub Actions run 41493502623](https://github.com/renbtg-cloud/demo-actv-dryqe/actions/runs/41493502623).
Milestones 23–25 below introduce additional contracts and acceptance tests;
these **must not be claimed as CI-passing until their own run completes**.

## Layout

- `core/`: admission and evidence contracts, policy and safe parsing.
- `store-postgres/`: durable PostgreSQL state, authorization, outbox and evidence.
- `runtime-rabbit/`: worker engine, RabbitMQ transport, HTTP provider transport and reconciliation.
- `adapter-springboot/`: Spring MVC application and Flyway migrations.
- `adapter-httpjdk/`: earlier independently verified HTTP transport (regression control, not the target deployment).
- `provider/`: independent SQLite-backed HTTP provider simulator.
- `simulator/`: multi-JVM input generator, profiles, coordinated shards, saturation explorer and comparison tools.
- `tests/`: offline and real-infrastructure regression and acceptance tools.
- `.github/workflows/spring-real-integration.yml`: builds and runs the actual Spring Boot integration test on a hosted runner with PostgreSQL and RabbitMQ services.

## Keycloak identity-provider integration (Milestone 19)

A new GitHub Actions matrix job (`oidc-keycloak`) tests the **real Spring OAuth2 Resource Server** against a disposable Keycloak authorization server. It obtains access tokens from the genuine `client_credentials` endpoint using per-run clients and realms, exercises issuer/audience/scope authorization, then verifies the durable activation through PostgreSQL, RabbitMQ and an independent SQLite provider. The real OIDC job passed **0/26 failures; 26/26 checks passed**. Combined with the seven prior real-Spring jobs, [GitHub Actions run 41469917139](https://github.com/renbtg-cloud/demo-actv-dryqe/actions/runs/41469917139) passed **0/238 failures; 238/238 checks passed**. See `docs/MILESTONE-19.md`. This is a CI-only local HTTP development server; **it is not a production IdP deployment**.


## Milestone 23 — Strict Unicode admission

Malformed UTF-8 byte sequences and unmatched UTF-16 surrogate escapes now
return 400 rather than becoming a replacement-character identity. Valid emoji
surrogate pairs remain valid JSON strings. See [Milestone 23](docs/MILESTONE-23.md).

## Milestone 24 — Explicit JSON charset contract

Only UTF-8 JSON is supported; contradictory/malformed charset parameters and
non-UTF-8 declarations return 415 before any database writes. See
[Milestone 24](docs/MILESTONE-24.md).

## Milestone 25 — Reject-before-side-effect HTTP regression

The real Spring JWT acceptance harness verifies malformed input and denied
routes/scope/method combinations cause no PostgreSQL admissions, including 24
concurrent invalid requests, before the known-good signed-provider activation.
See [Milestone 25](docs/MILESTONE-25.md).

## Milestone 21 — JWKS key rotation and issuer-outage security tests (verified)

The existing `jwt-security` integration harness now exercises an actual mutable
loopback JWKS endpoint: new RS256 key discovery without restarting Spring,
issuer outage while an unfamiliar key is presented, recovery, an overlap period,
concurrent signed requests, and issuer/audience denial after rotation.
This was verified with 0/31 failed integration assertions; 31/31 passed in the JWT CI job.
The script intentionally **does not claim immediate token revocation** when a
public key disappears from JWKS: resource-server JWKS caches may retain keys
until their TTL expires. No production key rotation is configured by this
fixture. See `docs/MILESTONE-21.md` for the test contract and verification gate.

## Milestone 22 — JWT attack vectors and bounded access-token lifetime (verified)

Spring's real JWT resource server is tested against unsigned `alg=none`,
RSA-public-material `HS256` confusion, genuinely signed but unapproved `RS512`,
attacker-controlled `jku` and embedded `jwk` headers. The `jku` test explicitly
checks that the attacker-controlled URL was **not fetched**. Tokens with missing
`iat`, `exp` or `sub`, nonpositive lifetimes, future issuance beyond 60 seconds,
or an excessive (`exp - iat`) lifespan must fail HTTP 401. A small positive
issuance skew remains permitted. The real Keycloak CI jobs remain in the matrix.

`APP_JWT_MAX_LIFETIME_SECONDS` defaults to **3600** and may be set to an integer
between **60 and 86400**. Tokens exceeding this limit are rejected even if the
issuer signs them. This is an intentional compatibility/security choice: align
it with the authorization server's actual access-token TTL; do not use it for
refresh tokens. Missing/malformed configuration fails startup. Key rollover
still does not provide instantaneous revocation of cached old signing keys.
See [`docs/MILESTONE-22.md`](docs/MILESTONE-22.md).

## Build and test the real Spring Boot service

Requirements: Java 21, Maven with dependency-repository access, Python 3.12+, `psql`, PostgreSQL 17, RabbitMQ 4.4, and the Python package `cryptography` for the independent test provider.

```bash
./scripts/test-spring-real.sh --output /tmp/deroyque-spring-acceptance.json
```

The harness creates a **unique temporary PostgreSQL schema** and a uniquely named RabbitMQ queue, requires Spring to execute Flyway migrations on the empty schema, launches the independently persisted HTTP provider, checks admission/idempotency/reconciliation, and drives additional admissions through the first-class multi-JVM simulator. It does not use Spring substitutes in this mode.

The GitHub Actions workflow invokes this command and provides PostgreSQL/RabbitMQ. A CI failure is evidence of an unresolved integration issue; do not treat the old JDK-server results as a substitute.

## Offline regression and diagnostics

```bash
./scripts/build.sh
./scripts/test-http-facade.sh
python3 -m unittest discover -s simulator/tests -v
```

The simulator supports CLI and YAML workload profiles, load generators in independent JVMs, deterministic sharding across hosts, saturating step tests and thread/JFR diagnostics. The optional **intra-service admission instrumentation is disabled by default** (`APP_LAB_METRICS_ENABLED=1` to opt in). All throughput figures obtained on a shared Linux are experimental; they do not establish standalone service capacity.

## Optional ready-to-use local Keycloak (Milestone 20)

**No existing Keycloak server is required** to try the OAuth2/OIDC integration. Docker is
needed only if you opt in. On the same machine where Deroyque is running:

```bash
python3 scripts/local_keycloak.py up
# On the same shell where the Spring Boot app will be launched:
eval "$(python3 scripts/local_keycloak.py env --provider local)"
# Start Deroyque with a working PostgreSQL/RabbitMQ and its normal JDBC/provider settings:
mvn -B -ntp -f adapter-springboot/pom.xml package
java -jar adapter-springboot/target/deroyque-springboot-0.10.0-SNAPSHOT.jar
```

The local issuer defaults to `http://127.0.0.1:18090/realms/deroyque-local`. The
application remains in **JWT mode**; `APP_JWT_ALLOW_HTTP_LOOPBACK=1` permits only
this **local** HTTP development provider. The realm has independent `reader`,
`writer` and `outsider` service-account clients, with actual Keycloak-generated
JWTs and real `activation:read` / `activation:write` scopes. Retrieve a short-lived
writer token to test a request without copying the client's secret:

```bash
TOKEN="$(python3 scripts/local_keycloak.py token --client writer)"
# Handle TOKEN as a credential: do not echo it or commit it.
python3 scripts/local_keycloak.py status
python3 scripts/local_keycloak.py down
unset TOKEN
```

To use your **own** OIDC-compatible identity provider instead, do not start local
Keycloak; configure the issuer/JWKS and audience explicitly (HTTPS is required):

```bash
eval "$(python3 scripts/local_keycloak.py env --provider external \
  --issuer 'https://idp.example/realms/your-realm' \
  --jwks-uri 'https://idp.example/realms/your-realm/protocol/openid-connect/certs' \
  --audience 'deroyque-activation-api')"
```

Both paths use the exact same Spring Security resource-server validation. The
local process and its disposable demonstration realm are **opt-in, off by default,
loopback-only, non-production**. Client credentials and realm files are generated
randomly under gitignored `.local/keycloak`, not committed. `down` removes the
container and temporary secrets. The local option passed **0/26 integration checks** with real Keycloak,
Spring Boot and the independent provider in GitHub Actions; all nine CI jobs
passed **0/264 failures**. See `docs/MILESTONE-20.md` for limitations,
troubleshooting, and [CI evidence](https://github.com/renbtg-cloud/demo-actv-dryqe/actions/runs/41475178125).

## Spring Security OAuth2/JWT authentication

The Spring Boot adapter now defaults to **JWT resource-server mode** (`APP_AUTH_MODE=jwt`). It will **fail startup without** `APP_JWT_ISSUER`, `APP_JWT_JWKS_URI` and `APP_JWT_AUDIENCE`. Tokens require a verifiable RS256 signature, matching issuer/audience, valid time claims, and the `activation:read` or `activation:write` scope for the matching endpoint. The application is only a resource server: use your trusted external identity provider or opt into the separate disposable local Keycloak described above. The main service never mints JWTs. Anonymous liveness/readiness are permitted; the laboratory ingress-metrics API is denied in JWT mode.

The old opaque bearer-token behavior is retained **only** when `APP_AUTH_MODE=lab` is explicitly selected. This is for local experiments and existing regression harnesses, not for deployment. JWKS over HTTP is rejected unless `APP_JWT_ALLOW_HTTP_LOOPBACK=1` and the URI targets loopback; this override is for explicitly enabled local development (including CI), never a remote HTTP identity provider.

## Security, guarantees and limitations

Do **not** deploy these test assets as a production service without further work. Laboratory bearer tokens, cryptographic fixtures and simulated provider receipts are for tests only. Exactly-once external side effects cannot be guaranteed against a provider that expires idempotency keys or lies about its effects. Consult `docs/STATUS-AND-GAPS.md` and the versioned milestone notes for demonstrated results, unresolved issues and the experimental history.


## Spring Boot multi-process adversarial CI

The GitHub Actions `Spring Boot real integration` workflow also runs an **opt-in**
second phase after the ordinary acceptance test. It starts a second real Spring Boot
JVM sharing the same PostgreSQL business state and RabbitMQ queue, starts a fresh
160-key / 960-POST workload using a memory-bounded number (maximum 24) of
**independent producer JVMs**, then kills the second Spring worker with `SIGKILL`
while admission is in progress. The surviving Spring JVM must complete the work.
The simulator audits HTTP outcomes, global idempotency, final states and producer
JVM overlap; the acceptance harness separately audits the provider SQLite effects.

The workload is **not** a performance benchmark of a production-sized instance.
A runner with insufficient memory fails the resource preflight rather than
silently reducing the test to two JVMs or disabling its memory safeguards.
The primary acceptance test is still runnable without the adversarial phase:

```bash
./scripts/test-spring-real.sh --load-jvms 8 --output /tmp/acceptance.json
```

To enable stress locally against isolated lab infrastructure and a working Maven
Central connection:

```bash
./scripts/test-spring-real.sh --adversarial-max-jvms 24 --output /tmp/adversarial.json
```

Crash injection does not prove the killed worker personally contacted the
provider before it died; it tests recovery from abrupt peer loss during a
concurrent workload. Idempotency guarantees still depend on the external
provider contract.


### Concurrent admission and crash-test configuration

`APP_ADMISSION_MAX_IN_FLIGHT` (default 32) bounds simultaneous HTTP admissions
per service JVM, reserving room for worker/database connections.
`APP_ADMISSION_WAIT_MS` (default 8000) is the maximum wait for an admission
slot; timeout responds 503 with an explicit same-key retry instruction.
This is admission backpressure, **not** optional observability.
`APP_LAB_DIAGNOSTICS_ENABLED=1` emits **only SQLState codes** for database
unavailability (OFF by default); no SQL, credentials, or request payloads.

The CI adversarial provider is explicitly configured for a finite 600-second
idempotency-key retention contract, with the application using a conservative
300-second authorization window. This is necessary for intentional JVM-crash
retries; retention=unknown would correctly pause uncertain attempts instead.
The simulator remains at 24 producer JVMs x 8 HTTP operations in the full
GitHub Actions stress plan; admission backpressure is exercised on the server.

## Milestone 14 — staggered multi-instance Spring failure test

GitHub Actions now runs **four truly independent Spring Boot JVM processes**
against the same PostgreSQL schema and RabbitMQ queue. A memory-aware number
of separate Java producer processes (up to 24) submits 960 real HTTP POSTs
sharing 160 idempotency keys. After observing durable admission and verified
worker activity on each victim, the harness issues two OS `SIGKILL` signals
to **different** Spring instances at distinct admission checkpoints; at least
two other Spring processes must remain alive. Auditors check all HTTP replies,
final states, overlapping producer JVMs, and exactly one SQLite provider
effect per accepted key. Workload thresholds, resource budget and crash
checkpoints are reported; runner capacity is not inferred.

Run in an isolated test environment:

```bash
./scripts/test-spring-real.sh --adversarial-max-jvms 24 --adversarial-service-jvms 4 --adversarial-crashes 2 --output /tmp/deroyque-m14.json
```

The incident tests survival of process loss during concurrent requests. It
**does not establish** that every killed JVM was between an external provider
side effect and local persistence at the instant of the kill. No claim of
unconditional external exactly-once delivery is made.

## Milestone 16 — Adversarial integration (CI)

The GitHub Actions matrix now includes real Spring Boot **competing recovery**, bounded PostgreSQL/RabbitMQ container interruptions and intentionally contradictory signed issuer claims. For methodology, failure semantics and precise limitations, see [Milestone 16](docs/MILESTONE-16.md). No success is claimed before the individual GitHub Actions jobs complete.


## Same-row contention acceptance (Milestone 17)

`tests/run_spring_same_row_race.py` drives four **independent real Spring Boot** consumer JVMs
onto a single PostgreSQL `READY` request while another PostgreSQL transaction holds
that **same row lock**. It publishes durable duplicate hints on RabbitMQ, records
which JVMs entered before lock release, then audits the winning commit, losing
claims, signed proof, settlement and independent provider effect. The new
`P21_TRACE_CLAIM_RACE=1` diagnostic is opt-in and does not gate production behavior.
The test seeds this specific internal request directly, without an outbox, so it
does **not** claim to re-test HTTP admission. See `docs/MILESTONE-17.md`.

Milestone 17 CI passed: 0/22 failures in the same-row case (22/22 passed),
0/189 failures across all six Spring Boot jobs (189/189 passed):
https://github.com/renbtg-cloud/demo-actv-dryqe/actions/runs/41451524902

The offline `scripts/build.sh` path additionally needs the included lab-only SLF4J
shim and two lab provider Java sources, plus separately provisioned PostgreSQL and
RabbitMQ client JAR dependencies. These external JARs are not bundled.
