# Milestone 10 — Spring Boot integration groundwork and live headless engine

**Delivery classification:** partially integrated, **not** a verified running Spring Boot service. The existing Java 21 service remains executable and its integration is revalidated on PostgreSQL 17.11/RabbitMQ 4.4.0/independent SQLite provider. Spring MVC sources, Maven build, Boot lifecycle wiring and Flyway integration were written but could not be compiled or executed against real Spring jars because this Linux has no Maven installation and cannot reach Maven Central. Direct controller methods were compiled with explicitly labeled **test-only stubs**, which does not prove Spring container semantics.

## Changes made in the same repository

- `P13Service.startEngine()` now starts the real RabbitMQ publisher/consumers, database policy registry, and recovery loop **without opening any JDK HTTP listener**; `start()` preserves the original JDK adapter and `close()` terminates only application-owned listeners, channels, broker connection and tasks. No shared database or broker server is shut down.
- The strict JSON decoder and opt-in `IngressMeasurements` moved from `adapter-httpjdk` to `core`, preserving the old adapter behavior. A new framework-independent `AdmissionHttpFacade` implements the Spring-boundary HTTP mapping using the existing `RequestOperations` API; it contains no Spring, JDBC or RabbitMQ imports. Duplicate-key rejection, bounded request bodies, independent read/write laboratory tokens, idempotent replays, errors and opt-in ingress metrics remain explicit.
- New `adapter-springboot/` contains a Spring Boot **4.1.2** Maven project, Spring MVC controller, Boot-managed worker lifecycle (no second JDK server), loopback binding by default, health/readiness endpoints, and two fresh-install Flyway migrations.
- `@DependsOn("flywayInitializer")` is intended to enforce Flyway-before-workers startup when the Boot runtime is available. **The actual Spring initializer ordering is unverified here.**
- PostgreSQL credentials are no longer hard-coded in `P13Service`: `P13_DB_USER`/`P13_DB_PASSWORD` are read from the environment (default laboratory user `nobody` and empty password).

## Fresh-schema PostgreSQL migrations

- `V1__initial_durable_engine.sql` reproduces the 10 `JdbcRabbitLedger.schema()` tables and their constraints. `V2__queue_and_evidence_indexes.sql` adds four PostgreSQL indexes. This is a **fresh-install baseline**, not a verified upgrade migration for any existing deployed schema; do not enable `baseline-on-migrate` casually.
- Both SQL files were executed against **real PostgreSQL 17.11 in a newly created laboratory schema**. Observed: 10 tables, 15 indexes in total (including primary/unique indexes), and 13 foreign keys. The original JDBC ledger then admitted one request, returned `COMMITTED`, reported `READY` and exposed its outbox event. This validates the SQL **manually**, not Flyway's runtime execution.

## Verified execution (separate complete test suites)

| Test | Failures | Passed | Environment |
|---|---:|---:|---|
| Existing JDK HTTP adapter contracts | 0/37 | 37/37 | Real Java, in-memory request port |
| Framework-neutral Spring admission façade | 0/33 | 33/33 | Real Java; 64 concurrent admission calls against test store |
| Spring MVC source signatures/direct methods | 0/9 | 9/9 | **Compile-only fake Spring/Servlet APIs** — not runtime Spring |
| Existing integrated end-to-end | 0/53 | 53/53 | Real PostgreSQL/RabbitMQ/Java and independent SQLite provider; two worker JVMs and a real crash |
| Headless engine lifecycle and work completion | 0/8 | 8/8 | Real PostgreSQL/RabbitMQ/SQLite, migrations-created schema, **no HTTP listener** |
| Independent input simulator tests | 0/52 | 52/52 | Python/Java simulator test suite |
| **Total** | **0/192** | **192/192** | Counts are assertions, not unique behaviors or production proof |

An intermediate **test assertion itself** expected incorrect JSON escaping (1/33 failure); it was corrected and the complete test suite rerun successfully. A separate manual migration check initially used an unsupported ledger CLI verb; the correct `count`, `admit`, `status` and `unpublished` verbs were then executed successfully. Neither failure should be misrepresented as proof of a production defect.

## Run the verified JDK/worker implementation on this Linux

```sh
./scripts/build.sh
./scripts/test-adapter.sh
./scripts/test-http-facade.sh
./scripts/test-spring-source-boundary.sh   # test doubles only; NOT Spring Boot runtime
python3 -m unittest discover -s simulator/tests -v
python3 -u tests/test_headless_engine.py    # requires local PostgreSQL, RabbitMQ and fixtures
export DEROYQUE_TEST_CP="$(./scripts/print-classpath.sh)"
python3 -u tests/run_end_to_end.py           # requires local PostgreSQL, RabbitMQ
```

## Build and launch the Spring Boot adapter on an environment with Maven Central

Requires Java 21, Maven 3.6.3+ and accessible managed dependencies, a running PostgreSQL server with a **new, explicitly created schema**, a running RabbitMQ, and an independent test provider. Create schema beforehand with the proper SQL permissions. Boot Flyway should create and index tables at startup (not yet verified here). Use the same schema in both JDBC URLs. Never point this experimental application at business or shared schemas.

```sh
export P13_JDBC='jdbc:postgresql://127.0.0.1:55432/postgres?currentSchema=your_new_schema'
export P13_DB_USER='your_pg_user'
export P13_DB_PASSWORD='your_pg_password'
export P13_QUEUE='deroyque.m10.isolated.queue'
export P13_PROVIDER_URL='http://127.0.0.1:18082/activate'
export P19_RECONCILE_URL='http://127.0.0.1:18082/lookup'
export APP_READ_TOKEN="$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')"
export APP_WRITE_TOKEN="$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')"
# Set P20_SIGNED_LOOKUP_REQUIRED, P20_PUBLIC_KEY_PEM, P20_EXPECTED_ISSUER,
# P20_EXPECTED_KID only with a verified matching provider key/contract.
# APP_LAB_METRICS_ENABLED remains 0 unless explicitly set to 1 by the evaluator.
mvn -f adapter-springboot/pom.xml clean package
java -jar adapter-springboot/target/deroyque-springboot-0.10.0-SNAPSHOT.jar
```

For the signed laboratory SQLite provider, follow `scripts/dev_demo.py` and `fixtures/keys` for local-only key paths and exact environment values. The Spring endpoint is loopback-only by default at `http://127.0.0.1:18081`, configurable with `SERVER_ADDRESS`/`SERVER_PORT`. The health endpoints `/api/v1/health/liveness`, `/api/v1/health/readiness` are explicitly implemented; the Boot Actuator exposes health/info by default.

## Security and incomplete work

- This is **not a production-certified Spring Boot app**. We have not run Maven, initialized the actual Spring context, exercised the embedded servlet server, run Flyway itself, or executed the simulator against a true Boot instance. All these are mandatory next acceptance gates.
- Authorization is still independent **static lab bearer tokens**, not OAuth2/JWT. No TLS, authorization roles, issuer/audience verification, secret management, deployment security, or full input resource limits are production-ready.
- Boot 4.1.2 was selected as a current maintained OSS branch, but the Spring starter dependency graph and lifecycle have only been externally checked at the project level, not resolved in this VM.
- The `P13Service` engine implementation still mixes worker policy, HTTP provider calls, broker consumers and scheduling. The **HTTP-framework lifecycle separation** has been verified, but a complete orchestrator refactor is still unfinished.
- Cross-host isolation, independent state-store backends, production evidence contracts and real exactly-once provider effect guarantees are not claimed. The provider may sign a false statement; signature validation cannot establish truth.
- This milestone uses no fake images or benchmark extrapolations. Previous simulator features and evidence remain included, but no new production throughput claim is made.
