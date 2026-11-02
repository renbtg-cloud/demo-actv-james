# Milestone 16 — concurrent recovery, temporary dependency outages, and contradictory signed evidence

This milestone adds three **real Spring Boot** GitHub Actions matrix scenarios using isolated PostgreSQL schemas, RabbitMQ queues, and a durable independent SQLite mock provider. The pre-existing 24-producer / four-service staggered-crash suite and the three exact-crash-window suite remain as separate regression jobs.

## Competing recovery (three exact fatal windows)

Each test kills a fault-armed Spring process with its genuine `Runtime.halt` code (81: after authorization, 82: after provider HTTP response, 83: after signed observation). It inspects durable PostgreSQL and SQLite state **before** launching three *concurrent, independent* Spring processes sharing the same schema and queue. It verifies readinesess, eventual `DONE`, at least one signed journal entry, durable settlement, immutable idempotency ID, and one external effect. Post-observation recovery must issue no additional provider POST. A process being ready does not prove it performed a competing claim; the test establishes concurrent, independently started contender availability and shared durable recovery, **not** proof that every contender acquired work. At the crash instant, the faulted instance is alone to ensure deterministic fault placement.

## Dependency interruptions

Two Spring processes run while the SQLite provider has already persisted an effect and the request remains `IN_FLIGHT`. The harness temporarily pauses the **CI-owned** PostgreSQL or RabbitMQ service Docker container for three seconds, unpauses it in a `finally` cleanup, and checks that the services survive, dependencies become ready again, the operation reaches `DONE` with signed evidence, and only one provider effect exists. This is a bounded container freeze, not a physical disk failure, permanent outage, network partition, or disaster-recovery test. No shared/user infrastructure is stopped.

## Late signed evidence and contradictory issuer claim

The provider first creates an actual external activation but intentionally omits the signature from lookup responses; the service must conservatively reach `PAUSED`, not invent success. A provider restart with the same SQLite ledger but normal Ed25519 signing allows a *later* signed lookup to settle as `DONE` without another external effect. Subsequently the laboratory issuer is intentionally configured to sign a **false `REJECTED` claim**, despite the real persisted `ACTIVATED` effect. A test-only Java probe invokes the **production** `P20SignedProof` verifier and `JdbcRabbitLedger.observeSignedLookup` to persist the fully validated (authentic, but dishonest) contradictory statement. The real watchdog then must record a `CONTRADICTORY_PROOF` incident and preserve the already terminal `DONE` status. A valid signature proves the issuer signed the statement, **not** that the statement describes reality.

This test deliberately injects a contradictory signed journal through a standalone Java test probe, rather than asserting that a spontaneous provider integration emits two opposing claims. The journal and reconciliation paths are real; the provider behavior is controlled by the harness.

## Scope, acceptance and secrets

The test returns nonzero if any check fails. It emits count-qualified `CHAOS_RESULT FAILURES X/N PASSED Y/N` and a JSON report without raw logs, bearer tokens, or provider private keys. Each scenario runs in GitHub Actions with Maven-resolved **real** Spring Boot, PostgreSQL 17, RabbitMQ 4.4, and the independent provider. Failure/approval claims must be based on completed job logs, never on source-only tests. Laboratory bearer authentication, signed proof and finite provider idempotency retention are not production identity and security assurances. The three scenarios complement rather than subsume the earlier suites.
