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
