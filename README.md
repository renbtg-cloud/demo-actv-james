# demoactv

> **Documentation preview.** This README describes the planned implementation. Application code, scripts, example environment files, and the other linked Markdown documents are not yet part of this preview. Links to the other Markdown documents are provisional until those files are created.

## Introduction

### Receiving and Persisting Activation Requests

`demoactv-service` is a Java 21 backend built with Spring Boot, Spring MVC, and Spring Data JPA/Hibernate. It accepts product activation requests, stores them durably, and arranges reliable delivery to an external provider. Its REST API exposes `POST /api/v1/requests` for creation and `GET /api/v1/requests/{id}` for status queries, secured with OAuth2/JWT and separate read/write permissions.

For a new request, the service validates `externalId`, `customerId`, and `productCode`, generates an internal UUID, and inserts an `activation_requests` row in PostgreSQL with `PENDING` status, `attempt_count = 0`, `state_change_counter = 0`, and equal `created_at` and `can_attempt_at` timestamps supplied by PostgreSQL. Idempotency is enforced through a unique `external_id` and `INSERT ... ON CONFLICT DO NOTHING RETURNING id`: a new request returns `202 Accepted` after commit, a matching duplicate returns `200 OK`, and a conflicting duplicate returns `409 Conflict`. The POST does not wait for the external provider.

### PostgreSQL as a Durable Work Queue

PostgreSQL also serves as the durable work queue, avoiding — **at the deliberately limited scale of this project** — the need for Kafka, RabbitMQ, or another intermediary. By default, four independent workers run in Java Virtual Threads and periodically look for eligible work.

A `PENDING` request is eligible when `can_attempt_at` has arrived and attempts remain; a `PROCESSING` request may be reclaimed after its lease expires. Eligible claim candidates are ordered by their eligibility instant, oldest first, with UUID as the tie-breaker. Each worker reads up to 20 candidates, then attempts a conditional JPQL update (compare-and-swap, or CAS) that rechecks eligibility and `state_change_counter`. On a successful claim, the service increments the attempt and state-change counters, assigns a fresh `claimToken`, sets `PROCESSING`, and establishes a 30-second lease. **The provider is contacted only after the claim transaction commits successfully.** A losing CAS is a normal concurrency outcome, not an application error.

### External Provider Integration and Retries

A worker holding a committed claim calls the provider using Spring `RestClient`, with explicit timeouts and **no open database transaction during HTTP I/O**. Every attempt for the same request sends the same `Idempotency-Key`: the persistent request UUID. HTTP `2xx` leads to `COMPLETED`; definitive failures lead to `FAILED`; transient failures and timeouts can schedule retries. The default limit is five claimed attempts, with configurable backoff, jitter, relevant `Retry-After` handling, and a Resilience4j Circuit Breaker.

An HTTP attempt is not the same thing as a business activation effect. Several attempts may occur, but preventing a second external effect depends on the **provider honoring idempotency and retaining its keys long enough**.

### Failure Recovery, Leases and Fencing

A 30-second lease allows another worker to reclaim abandoned work; no heartbeat is implemented in this version. Every new claim changes `claimToken` and increments `state_change_counter`, fencing off workers that have lost ownership. A stale worker cannot overwrite the newer owner's state. When an external call succeeds but PostgreSQL temporarily fails, the worker may make up to two additional **persistence attempts** (each a separate conditional update and commit) without calling the provider again. If a commit result is uncertain, a subsequent read reconciles the persisted state rather than assuming the update failed.

If persistence remains impossible, lease-based recovery takes over. When the final attempt's outcome cannot be confirmed, the recovery path can finish the request as `FAILED` with `OUTCOME_UNCONFIRMED`, instead of claiming that the external operation definitely failed.

### Observability, Infrastructure and Testing

The service is designed to remain observable, testable, and operationally predictable without unnecessary infrastructure. Spring Boot Actuator exposes liveness and readiness checks. Structured logs distinguish normal claim races, stale results, and genuine infrastructure failures. Readiness depends on PostgreSQL availability, not on the provider being reachable.

Flyway manages PostgreSQL schema changes, supported by database constraints and indexes. Startup configuration is validated fail-fast. Unit tests and integration tests using real PostgreSQL through Testcontainers cover state changes, concurrency, idempotency, and recovery. A **separate** persistent mock provider is used for integration testing, including ambiguous outcomes such as an activation succeeding just before an HTTP `500`. The mock's internal design and administrative endpoints are documented in [Mock Provider](docs/MOCK.md), not as features of the main service.

**Documentation links:** [SPEC.md](SPEC.md) · [AGENTS.md](AGENTS.md) · [ARCHITECTURE.md](docs/ARCHITECTURE.md) · [CLAIM.md](docs/CLAIM.md) · [MOCK.md](docs/MOCK.md) · [SECURITY.md](docs/SECURITY.md)


## Build, Deploy, Run and Basic Checks

### Fast Track

**Prerequisites:** Docker Engine with Docker Compose, plus a Bash shell. On Windows, use **Git Bash**. No host JDK or local PostgreSQL installation is required for this path.

From the repository root:

```bash
docker compose up --build -d
./scripts/dev-smoke-test.sh
```

The Docker builds compile the **main Java sources only** and package both executable JARs (`-Dmaven.test.skip=true`); they do not compile or run Maven tests. The smoke-test script waits for readiness, prints `Waiting for startup...` every two seconds, and fails with a clear message after a configurable timeout (60 seconds by default). Once ready, it generates a demo JWT and a fresh `externalId`, creates a request (`202`), waits for its terminal result, repeats the same request (`200`), and checks that a conflicting duplicate returns `409`. In the standard mock happy path, the terminal status is `COMPLETED`.

The smoke test **does not reset or modify the mock's simulation configuration**. If the mock was intentionally configured to fail or delay requests, the smoke test may legitimately fail. The procedure for temporarily restoring the mock's YAML configuration is described in Configuration Overview below.

### Maven Build

There are **two independent Maven projects**, without a root aggregator POM:

```bash
# Full build and tests (requires Docker for Testcontainers tests)
mvn -f demoactv-service/pom.xml clean verify
mvn -f demoactv-mock/pom.xml clean verify

# Compile tests, but do not execute them
mvn -f demoactv-service/pom.xml clean package -DskipTests
mvn -f demoactv-mock/pom.xml clean package -DskipTests

# Neither compile nor execute tests
mvn -f demoactv-service/pom.xml clean package -Dmaven.test.skip=true
mvn -f demoactv-mock/pom.xml clean package -Dmaven.test.skip=true
```

`-DskipTests` still compiles test sources; `-Dmaven.test.skip=true` skips both compilation and execution of tests. The stable executable names are `demoactv-service/target/demoactv-service.jar` and `demoactv-mock/target/demoactv-mock.jar`.

### Local Execution without Docker

**Prerequisites:** JDK 21, Maven, Bash (Git Bash on Windows), and an accessible PostgreSQL server. The server may be on the same machine or at any reachable host/port. Use separate terminals for the two applications; no local process-orchestration script is required.

First, configure the databases as described below. Copy the example environment files and edit them for your environment:

```bash
cp demoactv-service.env.example demoactv-service.env
cp demoactv-mock.env.example demoactv-mock.env
./scripts/dev-init-keys.sh
```

The `.env` copies contain your local connection settings and must not be committed. The key-initialization script generates a demo RSA pair only when neither key exists, reuses a valid existing pair, and fails on missing, damaged, or mismatched keys. Both local execution and Compose share `.local/dev-keys/`, which is ignored by Git.

Build both JARs with the Maven commands above. Then start **the mock in terminal 1**:

```bash
set -a
source ./demoactv-mock.env
set +a
java -jar demoactv-mock/target/demoactv-mock.jar
```

Start **the service in terminal 2**:

```bash
set -a
source ./demoactv-service.env
set +a
java -jar demoactv-service/target/demoactv-service.jar
```

The example files will be Bash-sourceable and provide database/schema credentials, the demo security profile and key path, and the provider URL. The provider URL is required; it is **automatically injected by Compose**, but must be configured for local execution or a real provider. Environment variables are the recommended configuration mechanism; Spring Boot CLI arguments remain an alternative. The two JARs can also be launched and debugged independently in IntelliJ IDEA.

### Reusing an Existing PostgreSQL Database

A dedicated database is **not** required. This is intentional: evaluators may reuse a database, PostgreSQL server, users, and schemas they already maintain, instead of installing or provisioning duplicate infrastructure. The two applications may even use the **same database, username, and schema**, provided their table names do not collide; Compose uses separate roles and schemas as its default arrangement.

The example environment files will accept independent JDBC URLs, credentials, and schema names. A small commented SQL setup example will show optional creation of the necessary schemas and permissions. Application startup runs each service's Flyway migrations, using **different Flyway history-table names** so both can share one schema. Setup instructions will not contain destructive `DROP DATABASE`, `DROP SCHEMA`, or `DROP TABLE` commands.

By default, Flyway refuses to migrate a nonempty schema that lacks its own history table. If you **intentionally** reuse such a schema, you may explicitly enable `spring.flyway.baseline-on-migrate=true` with `spring.flyway.baseline-version=0`. This removes an important safeguard: verify the JDBC URL and target schema before enabling it. If migrations encounter a real table-name or object conflict, startup fails; the applications do not rename or remove unrelated objects automatically.

### Basic Checks

The following **health paths** are available on the management port, `8081` by default, **without a JWT**:

```bash
curl -fsS http://localhost:8081/actuator/health
curl -fsS http://localhost:8081/actuator/health/liveness
curl -fsS http://localhost:8081/actuator/health/readiness
```

An `UP` readiness result means the service can access its PostgreSQL dependency. No other Actuator endpoints are exposed over HTTP by this project. In Compose, port 8081 is published to host **localhost only**.

For functional checks, run `./scripts/dev-smoke-test.sh`. It works with either Compose or local JARs and uses `SERVICE_BASE_URL` (default `http://localhost:8080`) for the REST API and `MANAGEMENT_BASE_URL` (default `http://localhost:8081`) for readiness. The script uses the ordinary API, not direct SQL access, and produces unique example request IDs each run. When the mock has a non-happy-path configuration, consult the application logs and the mock documentation rather than assuming the smoke test will always pass.

**Documentation links:** [SPEC.md](SPEC.md) · [AGENTS.md](AGENTS.md) · [ARCHITECTURE.md](docs/ARCHITECTURE.md) · [CLAIM.md](docs/CLAIM.md) · [MOCK.md](docs/MOCK.md) · [SECURITY.md](docs/SECURITY.md)


## Architecture at a Glance

`demoactv-service` accepts requests through a secured REST API, stores them in PostgreSQL, and processes them using independent database-backed workers. External HTTP calls occur only after durable claim commits; the provider is a separate integration boundary. This deliberately compact design is suited to the project's limited scale, with trade-offs explained in [Architecture](docs/ARCHITECTURE.md), the worker protocol in [Claim](docs/CLAIM.md), and the demonstration provider in [Mock](docs/MOCK.md).

**Documentation links:** [SPEC.md](SPEC.md) · [AGENTS.md](AGENTS.md) · [ARCHITECTURE.md](docs/ARCHITECTURE.md) · [CLAIM.md](docs/CLAIM.md) · [MOCK.md](docs/MOCK.md) · [SECURITY.md](docs/SECURITY.md)


## Configuration Overview

Runtime settings are supplied primarily through **environment variables**, with Spring Boot command-line properties as an alternative. The Docker Compose environment supplies working demonstration values. For a local run, use the two `.env.example` files as the complete, commented configuration catalogs and create local copies that stay outside version control.

| Setting | Purpose / default |
| --- | --- |
| JDBC URL, username, password and schema | Independent PostgreSQL connection settings for each application; set for local execution |
| Provider URL | **Required** for `demoactv-service`; injected automatically by Compose |
| Security profile | Explicit `demo` or `prod`; Compose uses `demo` |
| Worker count | 4 by default |
| Idle worker polling | 1 second by default |
| Claim lease | 30 seconds by default; no heartbeat |
| Maximum claimed attempts | 5 by default |
| Provider timeouts | Connect 2 seconds, read 5 seconds, overall deadline 10 seconds |
| Retry backoff and jitter | Configurable intervals and percentages |
| Circuit Breaker | Resilience4j, enabled by default |
| Mock idempotency retention | `0` minutes = eternal; a **mock startup** setting, not mutable through its admin PUT |
| Saved mock simulation configuration | Restored from PostgreSQL **by default**; explicit startup override to use YAML |

Invalid operational settings cause startup failure rather than silent correction. Authentication essentials are covered under Security below; see [Security Details](docs/SECURITY.md) for their implementation, and [Mock](docs/MOCK.md) for simulator-specific behavior and configuration snapshots.

### Temporarily Use the Mock's YAML Configuration

If an evaluator has stored a custom mock scenario in PostgreSQL but wants to run the standard happy path temporarily, explicitly start the mock with its saved-configuration flag disabled:

```bash
MOCK_SIMULATION_KEEP_MODIFIED_CONFIGS=false \
  docker compose up -d --force-recreate demoactv-mock

./scripts/dev-smoke-test.sh
```

This **does not delete** the stored JSONB configuration. On the next normal recreation without the override, the mock again prefers the saved configuration (`keep-modified-configs=true` by default). Counters and phase progress restart when the mock JVM restarts; activation and idempotency history remain in PostgreSQL.

**Documentation links:** [SPEC.md](SPEC.md) · [AGENTS.md](AGENTS.md) · [ARCHITECTURE.md](docs/ARCHITECTURE.md) · [CLAIM.md](docs/CLAIM.md) · [MOCK.md](docs/MOCK.md) · [SECURITY.md](docs/SECURITY.md)


## Security

The application uses **Spring Security as an OAuth2 Resource Server**, with JWT-based authentication and scope-based authorization. Authentication is enforced in both `demo` and `prod` profiles; there is no authentication-disabled mode.

For demonstration purposes, the project includes a self-contained JWT generation mechanism that does not require an external identity provider.

**Production mode requires an external OAuth2/OIDC identity provider, which this project does not supply.** Once its issuer, JWKS, audience, and required claims are configured, `demoactv-service` can validate its JWTs and authorize API operations.

### Security Profiles

Two explicitly selected security profiles are supported:

- **`demo`** — Uses locally generated RSA keys and RS256-signed JWTs. Intended for development, testing, and evaluation without an external identity provider. Docker Compose activates this profile automatically.
- **`prod`** — Validates JWTs issued by an independently provided and configured OAuth2/OIDC identity provider. The application does not implement, deploy, or manage that identity provider.

Exactly one security profile must be selected. Missing, invalid, or conflicting profile configurations cause startup to fail.

### API Permissions

| Endpoint | Required scope |
| --- | --- |
| `POST /api/v1/requests` | `activation.write` |
| `GET /api/v1/requests/{id}` | `activation.read` |

Scopes are independent: write permission does not implicitly grant read permission. Requests without a valid access token receive **HTTP 401 Unauthorized**; authenticated requests lacking the required scope receive **HTTP 403 Forbidden**.

### Demo Tokens

The repository includes Bash scripts for generating demonstration JWTs:

```bash
TOKEN=$(./scripts/dev-token-readwrite.sh 3600)
```

This generates a token valid for 3,600 seconds, with both read and write permissions. Additional scripts generate read-only, write-only, and no-scope tokens for authorization testing. Exactly one positive token lifetime, in seconds, is required by each script.

The demonstration RSA key pair is stored under `.local/dev-keys/` and excluded from version control. The service loads only the public key; the private key is used by the demonstration token generator. The scripts can use JDK 21 on the host or a helper container when Docker is available. Windows users can run the Bash scripts in Git Bash.

**Actuator health endpoints** (`/actuator/health`, `/actuator/health/liveness`, and `/actuator/health/readiness`) do not require JWT authentication. Under Compose, management port 8081 is bound to host localhost only; other Actuator endpoints are not exposed.

For external provider setup, JWT validation, key management, exposure controls, and security tests, see [Security Details](docs/SECURITY.md).

**Documentation links:** [SPEC.md](SPEC.md) · [AGENTS.md](AGENTS.md) · [ARCHITECTURE.md](docs/ARCHITECTURE.md) · [CLAIM.md](docs/CLAIM.md) · [MOCK.md](docs/MOCK.md) · [SECURITY.md](docs/SECURITY.md)


## Running Tests

The test suite uses JUnit 5, Maven Surefire and Failsafe, and descriptive `@Tag` annotations. Tests that use Testcontainers remain part of the **full validation** of the project but need not run during a memory-constrained local development session. Unless otherwise indicated, run the following commands for **each** Maven project.

### Full Test Suite

```bash
mvn -f demoactv-service/pom.xml clean verify
mvn -f demoactv-mock/pom.xml clean verify
```

Docker must be available for the Testcontainers integration tests.

### Tests without Docker

```bash
mvn -f demoactv-service/pom.xml clean verify -DexcludedGroups=testcontainers
mvn -f demoactv-mock/pom.xml clean verify -DexcludedGroups=testcontainers
```

These commands still run eligible unit and integration tests that do not depend on Testcontainers. The test plugins will be configured to honor JUnit tags.

### Fast Build without Tests

```bash
mvn -f demoactv-service/pom.xml clean package -Dmaven.test.skip=true
mvn -f demoactv-mock/pom.xml clean package -Dmaven.test.skip=true
```

This builds the application JARs without compiling or executing tests.

### Selective Test Execution

Tags describe independent dimensions such as `unit`, `integration`, `testcontainers`, `slow`, `claim`, `concurrency`, `idempotency`, `recovery`, and `security`.

```bash
# Exclude Testcontainers and slow tests
mvn -f demoactv-service/pom.xml clean verify "-DexcludedGroups=testcontainers | slow"

# Run claim tests not requiring Testcontainers
mvn -f demoactv-service/pom.xml clean verify "-Dgroups=claim & !testcontainers"
```

Use the same patterns for `demoactv-mock`. JUnit tags make selections more precise than package, class, or test-kind names alone.

### Important Integration Scenarios

The full suite will verify competing claims, lease recovery, duplicate API requests, access control, and ambiguous external responses. A particularly important test configures the mock to **activate the product but return HTTP 500**; the service retries with the same key and eventually reaches `COMPLETED` with two claimed attempts but **only one activation effect** at the provider. Details and rationale belong in [Claim](docs/CLAIM.md) and [Mock](docs/MOCK.md).

**Documentation links:** [SPEC.md](SPEC.md) · [AGENTS.md](AGENTS.md) · [ARCHITECTURE.md](docs/ARCHITECTURE.md) · [CLAIM.md](docs/CLAIM.md) · [MOCK.md](docs/MOCK.md) · [SECURITY.md](docs/SECURITY.md)


## Operations and Troubleshooting

Use these commands to inspect and control the Compose environment:

```bash
docker compose ps
docker compose logs -f demoactv-service
docker compose logs -f demoactv-mock
docker compose restart demoactv-service
docker compose down
```

`docker compose down` stops and removes Compose-managed containers and networks but **preserves named persistent volumes**. After restarting, requests, provider effects, and saved mock configurations remain available. For local JAR execution, inspect each terminal's logs and the health checks described under Basic Checks above.

For a failed startup, first check JDBC connectivity and schema permissions, the provider URL, JWT demo key integrity, or an invalid property. For a `401`, check the JWT; for a `403`, check the required scope. If readiness is `DOWN`, investigate PostgreSQL connectivity. A provider outage does not, by itself, make readiness `DOWN`.

### CAUTION: Destructive Reset — Permanent Data Loss

**WARNING:** The following command removes the Docker Compose environment **and its persistent volumes**. Activation requests, mock activation history, and saved mock configurations in the Compose-managed PostgreSQL volume will be lost:

```bash
docker compose down -v
```

**This operation is irreversible.** Use it only when deliberately resetting the demonstration database. For an ordinary shutdown that preserves data, use `docker compose down` without `-v`.

**Documentation links:** [SPEC.md](SPEC.md) · [AGENTS.md](AGENTS.md) · [ARCHITECTURE.md](docs/ARCHITECTURE.md) · [CLAIM.md](docs/CLAIM.md) · [MOCK.md](docs/MOCK.md) · [SECURITY.md](docs/SECURITY.md)
