# Milestone 42 — Real Spring/PostgreSQL/RabbitMQ/SQLite gateway experiment

A dedicated **real integration** CI scenario starts two distinct Spring Boot 4 JVM OS processes using a fresh PostgreSQL schema and shared RabbitMQ queue, plus the separately persisted SQLite HTTP provider. A loopback witness distributes an 80-POST / 20-shared-key workload from two independent coordinators and four Java producer JVMs. It retrieves the actual per-JVM server metrics, then checks all their totals, the gateway-clock report, producer records, final GET states and exactly 20 SQLite effects under the provider test's retention contract.

This experiment targets the **actual Spring Boot application**, unlike the offline stub test in M41. The workflow validates that the two Spring processes each received POSTs and have distinct per-JVM identities. The single witness rate is not interpreted as provider completion rate or isolated backend throughput; the test intentionally detects `SAME_KERNEL_OBSERVED` on GitHub's single-host CI runner.

CI invocation: `python3 -u tests/run_spring_gateway_witness.py --boot-jar adapter-springboot/target/deroyque-springboot-0.10.0-SNAPSHOT.jar --output "$RUNNER_TEMP/spring-gateway-witness.json"`. Maven Central, PostgreSQL 17, RabbitMQ 4.4, `psql`, Python `cryptography`, and Java 21 are required. Unlike the offline M41 lab, this claim must remain **pending until the actual GitHub job succeeds**.

## Completed M42 evidence

[GitHub Actions run 41511868650](https://github.com/renbtg-cloud/demo-actv-dryqe/actions/runs/41511868650) finished with **0/13 failed jobs, 13/13 passed**. The real `gateway-witness` job reported **0/19 failed assertions, 19/19 passed**; the fail-closed evidence gate separately accepted 19/19. This is the verified M42 baseline. The additional checks in M44–48 require a separate CI run and are not retroactively claimed here.
