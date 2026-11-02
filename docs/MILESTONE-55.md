# Milestone 55 — Signed observation and dispatch lineage

## Demonstration objective

A valid Ed25519 signature alone does not mean that the token was attached to the correct observation. The SQL audit follows attestation OID → observation, attestation DID → dispatch, and dispatch RID → request. It requires an actual signed-provider observation at HTTP 200 with the expected positive action/evidence class, and no mismatched observation DID/source. This is a *relational provenance* check, not an independently trusted source of the provider's truth.

## Reproduction and limits

Run `python3 -m unittest discover -s tests -p "test_gateway_*audit.py" -v` and `python3 -m unittest discover -s tests -p test_ci_evidence_gate.py -v`. Full approval requires the real Spring integration workflow and its PostgreSQL, RabbitMQ and SQLite-backed provider, including all gateway scenarios. New positive proofs are scoped to the disposable two-Spring-JVM same-host lab; they do not certify production readiness, physical host independence or universal external exactly-once execution.
