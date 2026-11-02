# Milestone 59 — Mutation controls for the complete proof chain

## Demonstration objective

The standalone tests deliberately fabricate missing, duplicated, swapped and orphaned identities, wrong terminal stages, unsigned-link mismatches, expired indexes, extra effects and inconsistent time bounds. The audit must reject each mutation. These tests run without PostgreSQL, a Spring JVM or RabbitMQ, and therefore complement rather than replace the real integration jobs.

## Reproduction and limits

Run `python3 -m unittest discover -s tests -p "test_gateway_*audit.py" -v` and `python3 -m unittest discover -s tests -p test_ci_evidence_gate.py -v`. Full approval requires the real Spring integration workflow and its PostgreSQL, RabbitMQ and SQLite-backed provider, including all gateway scenarios. New positive proofs are scoped to the disposable two-Spring-JVM same-host lab; they do not certify production readiness, physical host independence or universal external exactly-once execution.
