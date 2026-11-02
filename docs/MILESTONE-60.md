# Milestone 60 — Fail-closed real Spring CI proof gate

## Demonstration objective

The short gateway workload, 120-second sustained workload and all three repeated real experiments now invoke the stronger read-only PostgreSQL and SQLite audits *after* their previous reconciliation. Three additional explicit result flags are required by the CI evidence gate; a zero process exit or credible aggregate counts without these proofs is insufficient. All genuine CI scenarios retain their existing infrastructure, security boundaries and negative controls. This milestone passes only after the new GitHub Actions run is green; local unit tests do not count as real Spring acceptance.

## Reproduction and limits

Run `python3 -m unittest discover -s tests -p "test_gateway_*audit.py" -v` and `python3 -m unittest discover -s tests -p test_ci_evidence_gate.py -v`. Full approval requires the real Spring integration workflow and its PostgreSQL, RabbitMQ and SQLite-backed provider, including all gateway scenarios. New positive proofs are scoped to the disposable two-Spring-JVM same-host lab; they do not certify production readiness, physical host independence or universal external exactly-once execution.
