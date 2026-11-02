# Integrated Milestone 03 — Ingress Simulator

This is an incremental update to the runnable Milestone 02 architecture. The service still runs on Java 21 with a JDK HTTP adapter (not yet Spring Boot), PostgreSQL and RabbitMQ. The Mock Provider remains an independent SQLite HTTP process. The new first-class ingress simulator is a Java HTTP load agent supervised by a Python runner.

## Implemented and executed

- 7/7 simulator tests passed, including fake-service admission races, rejection for bad credentials, opt-in protection for remote targets, duplicate-acceptance auditor tests, percentile computation, missing-creation detection and unique-record state counting.
- Actual service run: 8 producer JVMs, 320 POSTs, 128 in-flight HTTP (observed), 8 active JVMs, no unexpected statuses.
- Actual service run: 32 producer JVMs, 960 POSTs, 256 in-flight HTTP (observed), 32 active JVMs, no unexpected statuses.
- In both load cases, the audited identity invariants and GET immutable-field checks passed. During the runs some activation requests remained pending and are not counted as completed external effects.

## Correctness and reporting change

A prior simulator revision counted every GET polling observation in `verified_states`, inflating the state histogram. This milestone counts each unique request at most once (the last state observed). Missing creation in the current run is now reported when a replay or payload conflict is observed. Re-ran both real load scenarios after the fix.

## What has not been delivered

YAML configuration, traffic-profile scheduler, configurable Java heap/CPU, automatic JVM scaling threshold exploration, sustained soak/saturation benchmarks, multi-host benchmarking, systematic performance run-to-run comparisons and production deployment. The simulator is designed to be extended in place.

## Scope disclaimer

All producer JVMs and infrastructure were on the same physical execution host. This exercises independent Java processes and real database/broker contention but does not simulate multiple physical fault domains. `PASS` means the stated assertions and allowed response codes succeeded; it does not establish global exactly-once provider execution.
