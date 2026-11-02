# Milestone 56 — Schema-wide contamination and orphan detector

## Demonstration objective

Per-key counts can miss additional rows from another key. In this deliberately fresh per-test PostgreSQL schema, compare all requests, dispatches, settlements and signed attestations against the expected unique-key count, and demand zero incidents and rejected proofs for the uncontaminated positive-flow baseline. This requirement is intentionally specific to this disposable fixture, **not** a production-wide invariant: production systems can legitimately contain multiple dispatches, retries and rejections.

## Reproduction and limits

Run `python3 -m unittest discover -s tests -p "test_gateway_*audit.py" -v` and `python3 -m unittest discover -s tests -p test_ci_evidence_gate.py -v`. Full approval requires the real Spring integration workflow and its PostgreSQL, RabbitMQ and SQLite-backed provider, including all gateway scenarios. New positive proofs are scoped to the disposable two-Spring-JVM same-host lab; they do not certify production readiness, physical host independence or universal external exactly-once execution.
