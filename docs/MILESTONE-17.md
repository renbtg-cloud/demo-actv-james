# Milestone 17 — empirically observed contention of the SAME PostgreSQL request row

## Gap addressed

Milestone 16 proved that three independent Spring Boot JVMs were online during recovery, but not that different JVMs actually contended on the **same** request record at the same instant. The new integration test uses real workers and the real transactional claim path, not a mocked ledger or thread pool.

## Scenario

- Four distinct **Spring Boot application processes**, each with its actual RabbitMQ consumers, run against one disposable PostgreSQL schema and one quorum queue.
- The test creates a single `READY` request directly in the database **for this internal claim test**. It intentionally creates **no outbox row**; this is not advertised as a successful public API admission.
- An independent PostgreSQL transaction acquires `SELECT ... FOR UPDATE` on this exact request row. The test verifies that the lock has actually been taken.
- The test publishes 96 durable duplicate hints containing the exact same request UUID to the RabbitMQ quorum queue using `pika`. The application has a test-only `P21_TRACE_CLAIM_RACE=1` diagnostic; without the flag, no new output is generated and no synchronization is inserted in the production claim algorithm.
- While the row is locked, the test **requires four separate JVM logs** to show that a real consumer entered the *same* `READY` request. The request must remain `READY` with zero dispatches.
- It releases the lock and verifies a `DONE` state, exactly one committed claim from the JVM diagnostic, at least two `STALE` claim results, one authorization/dispatch, one signed attestation and settlement, and one external activation in SQLite. It also checks that all four application JVMs survive and the public GET API reads the same durable result.

Evidence is stronger than "four JVMs started," but limited: `P21_RACE_ENTER` proves entry before the row lock is released, rather than an exact CPU-instruction-level simultaneous attempt. Log outcomes are corroborated with database state. The test uses fixture keys and local bearer tokens; never upload raw logs. The provider idempotency contract remains necessary for any external-effect claim.

## Execution

`Spring Boot real integration` has a sixth, isolated matrix case, `same-row-contention`:

```bash
mvn -B -ntp -f adapter-springboot/pom.xml clean package
python3 -m pip install 'cryptography>=42,<50' 'pika==1.3.2'
python3 -u tests/run_spring_same_row_race.py \
  --boot-jar adapter-springboot/target/deroyque-springboot-0.10.0-SNAPSHOT.jar \
  --output /tmp/spring-same-row-race.json
```

Requires PostgreSQL 17, RabbitMQ 4.4 and `psql`, plus the service credentials configured in CI. The test never pauses or stops external shared PostgreSQL/RabbitMQ services. Each run creates a separate schema and queue. As in prior milestones, a green job is a specific experimental result, not proof of universally exactly-once side effects.

## Actual observed CI result

GitHub Actions run: https://github.com/renbtg-cloud/demo-actv-dryqe/actions/runs/41451524902

- **New same-row contention test:** 0/22 failed; 22/22 passed. Four distinct real Spring Boot JVMs entered the same held row; exactly one committed claim and **seven** `STALE` claim outcomes were observed; one signed proof, one settlement and one independent provider effect followed.
- **Full six-job Spring regression:** 0/189 failed; 189/189 passed (22 same-row + 16 late/contradictory + 44 competing windows + 44 exact fault windows + 44 staggered crashes + 19 dependency interruptions).
- **Original first CI attempt:** failed at provider `/health` because the new harness incorrectly expected JSON rather than plaintext. Corrected in commit `ed68992e3b5408019a7e752daac0836daaa5d42c`; the follow-up run above passed.
- **Extracted ZIP verification:** `./scripts/build.sh`, 33/33 facade checks and 52/52 simulator checks passed. Five test-only JVM sources missing from the previous packaging manifest (3 SLF4J shim files and 2 lab Java provider files) are restored to the integrated source tree. The offline build still requires locally supplied PostgreSQL and RabbitMQ client JAR dependencies, as explicitly stated by `scripts/build.sh`; these dependencies are not embedded in the ZIP.

The `P21_RACE_ENTER` diagnostic marks entry before a blocked row-level operation. It does **not** imply simultaneous execution of the same bytecode instruction, nor does it prove 7 distinct losing JVMs (the losers are individual claim attempts). The stronger result is four JVMs each entered the same row before release, combined with one committed claim and seven STALE attempts in aggregate.
