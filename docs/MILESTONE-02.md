# Integrated Milestone 02 — implementation report

## Actual code changes

1. Split the Milestone 01 Java source directory into separate `core`, `store-postgres`, `adapter-httpjdk`, `runtime-rabbit` and `lab-java-providers` compile units; each module is built with only its direct compile dependencies.
2. Added `RequestOperations`: transport-neutral commands, DTOs and admission results. This API has no JDBC or AMQP type dependencies.
3. Implemented `RequestOperations` in `PublicRequests`, preserving PostgreSQL atomic admission and strict idempotency. A failed or uncertain PostgreSQL COMMIT maps to `UNCERTAIN` instead of assuming request absence. Checked JDBC failures become `DataAccessUnavailable` at the application boundary.
4. Modified `RestApi` to depend only on `RequestOperations` and core DTOs. No PostgreSQL details leak into HTTP mapping. A standard 503 is returned for storage-unavailable/commit-uncertain outcomes, preserving the caller's ability to retry the **same** external ID.
5. Added shell scripts for independent JAR builds, a modular runtime classpath, a runnable JAR-based service, and self-contained HTTP contract tests with an in-memory service.
6. Preserved the legacy compatibility classpath for prior experimental tests. The new JAR-only end-to-end suite validates the actual modular distribution.

## Test architecture and boundaries

The adapter-only test compiles and runs with *only* `core.jar` and `adapter-httpjdk.jar`. It does not substitute a fake JDBC jar or runtime. For the end-to-end suite, `DEROYQUE_TEST_CP` explicitly selects the separate JARs, PostgreSQL JDBC and RabbitMQ client. The persistence engine and two Java JVM workers connect to real PostgreSQL and RabbitMQ. Provider effects are stored independently in SQLite. A worker crash is a genuine JVM termination.

| Completed suite on final source | Failed/total | Passed/total |
| --- | ---: | ---: |
| HTTP adapter in-memory contract | 0/22 | 22/22 |
| JAR-only full integration | 0/53 | 53/53 |
| Signed-evidence targeted regression | 0/15 | 15/15 |
| **Total** | **0/90** | **90/90** |

The tests of deliberate provider lies or contradictions pass when those unsafe or conflicting situations are **correctly detected or reproduced**. None of these tests proves globally exactly-once processing.

## Engineering constraints still open

- This is modular on the classpath, not JPMS (Java Platform Module System). Classes remain in the legacy `demo` package to preserve existing cross-JAR package-private behavior while future internal APIs are extracted. Do not call this strong runtime encapsulation.
- The main worker class still controls RabbitMQ channels, API server, timers and provider client together; Spring Boot cannot simply replace it without further decomposition.
- The Java standard library is used for HTTP; Spring libraries could not be installed/verified in this offline environment. Dependency pinning, proper logging, Flyway and secure OAuth2/JWT are next milestones, not delivered claims.
- Test keys and SLF4J shim are for laboratory use only. The demo uses fixed loopback/broker assumptions and is not ready for arbitrary hosts or production.
- SQLite is used by the **simulated external provider**, not as a demonstrated alternative Deroyque State Store.

## Next integrated work (no more tiny numbered POCs)

1. Extract provider `HttpClient` gateway and RabbitMQ worker/dispatcher control from `P13Service` into separately testable classes while retaining the same end-to-end harness and single evolving repository.
2. Adopt a managed dependency build when the artifacts can actually be resolved, then implement Spring Boot with OAuth/JWT at the `RequestOperations` boundary; keep auth and admission semantics covered by equivalent HTTP tests.
3. Move schema creation to migrations, introduce operational configuration, and validate restart/recovery across upgrades.
4. Finish the standalone PostgreSQL queue engine and independent HSQLDB conformance for the Rabbit State Store.
