> **Historical document — milestone 01.** For the current integrated code and verified results, see [Milestone 02](MILESTONE-02.md) and the root [README](../README.md).

# Integration status, risks and next engineering work

## Demonstrated in the integrated milestone

- **Single source tree** containing a Java 21 service, PostgreSQL authoritative ledger, RabbitMQ push consumers / outbox, signed evidence and a separately persisted Python/SQLite provider.
- Atomic public JSON admission: distinct idempotency ID, immutable customer and product fields, policy revision, request and initial outbox notification in one PostgreSQL transaction.
- Read/write development tokens on the new API, with legacy test routes blocked when public API is enabled. JDK HTTP server binds loopback only.
- Multiple simultaneous JVM workers on one durable state store and quorum queue, with live PostgreSQL and broker, concurrent requests and durable signed reconciliation.
- Real crash of Java worker between external HTTP response and observation COMMIT; new process recovered outcome with a signed lookup, leaving one SQLite provider effect in that **specific** scenario.
- Verified 0/53 failures (53/53 pass) in the final integrated suite, plus 0/15 failures (15/15 pass) in targeted P20 checks; do not count the interrupted wider P20 run as complete.

## Nonclaims / debt

1. **NOT Spring Boot, NOT finished OAuth/JWT security.** Source for a full Spring app was not present in the old documentation ZIP. JDK `HttpServer`, simple request parser, development bearer credentials, no TLS, hardcoded DB/broker lab connection settings and local SLF4J shim are not a deployable edge.
2. No semantic guarantee of globally exactly-once HTTP execution. An expired provider idempotency key with post-preflight JVM pause can duplicate a remote effect. Nor does Ed25519 prove a supplier tells the truth.
3. `JdbcRabbitLedger` remains PostgreSQL-specific in several operational paths. The HSQLDB 2.7.4 portability POCs do not establish that this **combined** engine runs there. No SQLite, RocksDB or standalone PostgreSQL-only worker engine delivered in this repository.
4. No proper migrations/versioned schema management, no standardized Maven/Gradle build, and no containerized one-command infrastructure setup. Current `schema` operation is for a **new empty schema** only. No zero-downtime upgrade strategy.
5. Application logging is carried over from lab experiments; no production structured logging, trace IDs, credentials/key rotation, audit authorization, operational runbooks, observability/SLOs or systematic load testing.
6. GET is guarded only by a global read token, with no tenant-specific authorization. Do not put real personal data into this laboratory.
7. The current provider simulator stores remote effects independently in SQLite but runs on the same Linux host; no independent physical failure domain or external provider SLA. Provider private key bundled **only** to reproduce negative tests.
8. Worker loops and transport resilience are inherited from P20 and require a consolidated, independent architectural audit. Specifically examine Rabbit client channel lifecycle, cancellation and safe shutdown, outbox claim distribution, database connection pooling, pause scheduling and error policy.
9. The test-only shim under `lab/slf4j-test-only` is a deliberate compatibility workaround; replace with real SLF4J and dependencies for a production build.
10. Test suite is substantial but bounded: 53 assertions are not a formal correctness proof, performance benchmark or certification. Some strong assertions check an intentionally unsafe system response.

## Next integration iteration (not another numbered POC)

**First:** refactor legacy experiment classes into clean core/storage/broker/provider/adapters with stable APIs while retaining existing regression tests; implement versioned Flyway migrations and a reliable build. **Second:** replace the JDK HTTP adapter with real Spring Boot/JWT and proper access control, retaining the exact admission semantics and running the same integrated fault harness. **Third:** complete independently runnable PostgreSQL queue backend and make HSQLDB a genuinely verified alternative State Store for the Rabbit variant. Later consider SQLite single-host and RocksDB as separate backend projects only after conformance is real.
