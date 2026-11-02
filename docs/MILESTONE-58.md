# Milestone 58 — Cross-store signed evidence time ordering

## Demonstration objective

The Ed25519 token already binds its signed timestamp to PostgreSQL metadata. An additional check binds that **verified** timestamp to the independent provider's effect creation and idempotency expiration window (`created_ms <= signed_at_ms < expires_ms`). These actors share a kernel and clock in the current CI fixture, making this a valid local consistency test; the same inequality cannot safely be imposed on physically separate hosts without clock uncertainty bounds. No global database transaction or remote exactly-once claim follows.

## Reproduction and limits

Run `python3 -m unittest discover -s tests -p "test_gateway_*audit.py" -v` and `python3 -m unittest discover -s tests -p test_ci_evidence_gate.py -v`. Full approval requires the real Spring integration workflow and its PostgreSQL, RabbitMQ and SQLite-backed provider, including all gateway scenarios. New positive proofs are scoped to the disposable two-Spring-JVM same-host lab; they do not certify production readiness, physical host independence or universal external exactly-once execution.
