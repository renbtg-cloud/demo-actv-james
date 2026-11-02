# Milestone 12 — Real Spring Boot acceptance gate (partially completed)

## Engineering result

The previous Spring integration was **source-only**. This milestone creates an executable acceptance path that **cannot silently fall back to the JDK HTTP adapter** in its Spring mode. It prepares an independent PostgreSQL schema, independent SQLite HTTP provider, unique RabbitMQ quorum queue, and real JVM-generated HTTP load. Only a *real Spring Boot executable JAR* is accepted for `--mode boot`.

The main environmental limitation remains: the current lab Linux has Java 21, PostgreSQL 17.11, RabbitMQ 4.4.0 and Python but **no Maven, no Spring JARs, and no DNS access to Maven Central**. We therefore did **not** execute a real Spring Boot build, auto-configuration, Flyway or MVC request in this lab. The checked-in GitHub Actions workflow was **not run**.

## What was executed locally

- **0/25 failures; 25/25 checks passed** in the **legacy control**, which validates the *new acceptance test itself*, not Spring. This includes 8 separately launched generator JVMs, 160 POST attempts, 40 unique keys, conflicts, accepted replays and final state verification.
- **0/53 failures; 53/53 checks passed** in the pre-existing real-infrastructure E2E regression, including independent PostgreSQL, RabbitMQ, SQLite, actual JVM crash, and signed reconciliation without another external effect.
- **0/52 failures; 52/52 passed** in the input simulator regression suite.
- **0/88 failures; 88/88 passed** in the Java HTTP-adapter (37), framework-neutral facade (33), and provider transport (18) suites.
- **0/9 failures; 9/9 passed** in the Spring API source test with **stand-in interfaces** — not counted as real Spring testing.
- Boot-mode preflight correctly refused an absent executable JAR with **exit code 3 (BLOCKED)** and executed **0/0 test assertions**.

### What the real Boot mode will require

1. A genuine Maven build of the `adapter-springboot` module with Spring Boot 4.1.2 dependencies.
2. Start with a freshly created PostgreSQL schema that contains **no application tables**; do **not** run the legacy `JdbcRabbitLedger.schema()` initializer.
3. Require that **Flyway runs as part of actual Spring startup**, creating and recording V1/V2 before the engine starts. Verify `flyway_schema_history` for applied migrations.
4. Verify readiness, default-disabled ingress metrics, authentication boundaries, HTTP media/body validation, idempotency and conflicts, durable completion, signed proof storage, exactly one effect in the mock for the tested unique identity.
5. Run the independent input simulator against the **Spring HTTP listener**, with 8 producer JVMs, 160 POSTs, 40 shared keys, and verify final state and idempotency invariants. Passing a source-stand-in compilation alone cannot satisfy this gate.

## RabbitMQ CI configuration

`RabbitDelivery` previously hard-coded `127.0.0.1:5672 guest/guest`, which is unsuitable for CI and remote broker deployments. It now accepts `P13_RABBIT_HOST`, `P13_RABBIT_PORT`, `P13_RABBIT_USER`, and `P13_RABBIT_PASSWORD`, with the old settings retained **only as local lab defaults**. This path was regression-tested against the existing live local RabbitMQ. The custom CI broker credentials path has not yet been executed.

## Reproduction on an isolated CI machine

- Use a dedicated throwaway PostgreSQL and RabbitMQ service account, Java 21, Maven, psql, and Python with `cryptography`.
- Execute `./scripts/test-spring-real.sh --output /tmp/deroyque-spring-real.json` with the `DEROYQUE_PG_*` environment variables configured.
- Alternatively, run the **unexecuted** `.github/workflows/spring-real-integration.yml` workflow on a GitHub Actions runner with network-enabled Maven resolution. It is a proposed validation pathway, not proof that the build succeeds.
- Results contain assertion names/counts, not bearer tokens or the private key. Keep raw application logs and lab credentials private. The fixture Ed25519 key is explicitly **for tests only**.

## Caution

These tests do not prove exactly-once remote effects in all conditions. The external provider's signed declarations may be false even when signatures verify, and the time-bound idempotency gaps demonstrated by P17 remain relevant.
