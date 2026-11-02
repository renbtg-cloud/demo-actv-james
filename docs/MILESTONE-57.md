# Milestone 57 — Independent SQLite idempotency index attestation

## Demonstration objective

The external provider has two separately persisted representations: `effect` and `idem_index`. A read-only auditor now requires one key in each, generation 1, activated outcome and no extras in the isolated provider database. A count of activated effects alone would not expose a divergent provider idempotency index. Tests mutate both independently, including an extra effect, a stale index and an inconsistent generation.

## Reproduction and limits

Run `python3 -m unittest discover -s tests -p "test_gateway_*audit.py" -v` and `python3 -m unittest discover -s tests -p test_ci_evidence_gate.py -v`. Full approval requires the real Spring integration workflow and its PostgreSQL, RabbitMQ and SQLite-backed provider, including all gateway scenarios. New positive proofs are scoped to the disposable two-Spring-JVM same-host lab; they do not certify production readiness, physical host independence or universal external exactly-once execution.
