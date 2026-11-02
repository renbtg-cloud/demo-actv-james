# Deroyque / demoactv — experimental distributed activation engine

> **Asynchronous fault-tolerant product activation**

This repository contains the **actual experimental source code**, not the older documentation-only preview. The three primary deliverables are the asynchronous activation service, an independent HTTP provider simulator, and a configurable multi-JVM incoming-transaction simulator. They are developed as one evolving system.

**Status (2 November 2026):** the real Spring Boot 4.1.2 and Flyway V1/V2 passed six adversarial GitHub Actions jobs (including four JVMs genuinely contending for the same DB row). Milestone 18 adds fail-closed JWT resource-server security as the Spring deployment default, verified in a seventh CI job (0/23 failures); all seven real Spring jobs passed (0/212 failures): [GitHub Actions run 41456694967](https://github.com/renbtg-cloud/demo-actv-dryqe/actions/runs/41456694967).

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

A separate GitHub Actions matrix job (`oidc-keycloak`) now tests the **real Spring OAuth2 Resource Server** against a disposable Keycloak authorization server. It obtains access tokens from the genuine `client_credentials` endpoint using per-run clients and realms, exercises issuer/audience/scope authorization, then verifies the durable activation through PostgreSQL, RabbitMQ and an independent SQLite provider. See `docs/MILESTONE-19.md`. This is a CI-only local HTTP development server; **it is not a production IdP deployment**.

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

## Spring Security OAuth2/JWT authentication

The Spring Boot adapter now defaults to **JWT resource-server mode** (`APP_AUTH_MODE=jwt`). It will **fail startup without** `APP_JWT_ISSUER`, `APP_JWT_JWKS_URI` and `APP_JWT_AUDIENCE`. Tokens require a verifiable RS256 signature, matching issuer/audience, valid time claims, and the `activation:read` or `activation:write` scope for the matching endpoint. No token minting or OAuth2 authorization server is bundled: use a trusted external identity provider and a configured HTTPS JWKS endpoint. Anonymous liveness/readiness are permitted; the laboratory ingress-metrics API is denied in JWT mode.

The old opaque bearer-token behavior is retained **only** when `APP_AUTH_MODE=lab` is explicitly selected. This is for local experiments and existing regression harnesses, not for deployment. JWKS over HTTP is rejected unless `APP_JWT_ALLOW_HTTP_LOOPBACK=1` and the URI targets loopback; this override is for the CI's temporary key server only.

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
