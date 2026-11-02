# Deroyque / demoactv — experimental distributed activation engine

> **Asynchronous fault-tolerant product activation**

This repository contains the **actual experimental source code**, not the older documentation-only preview. The three primary deliverables are the asynchronous activation service, an independent HTTP provider simulator, and a configurable multi-JVM incoming-transaction simulator. They are developed as one evolving system.

**Status (2 November 2026):** the Java 21 JDBC/RabbitMQ engine and the legacy JDK HTTP adapter have been exercised with real PostgreSQL, RabbitMQ and an independent SQLite-backed mock provider. A Spring Boot adapter, Flyway V1/V2 migrations and real-runtime acceptance harness are present, but a successful Spring Boot startup and integration test are **not yet established** until GitHub Actions completes.

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
