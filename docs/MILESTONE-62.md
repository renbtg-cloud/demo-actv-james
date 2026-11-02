# Milestone 62 — Independent PostgreSQL materialized payload audit

## Purpose and implementation

For each expected durable key in the isolated Spring integration test, query `customer_id` and `product_code` directly from PostgreSQL and compare to the independently observed winning variant from the global simulator report. Both `standard` and `different` can be valid winning variants when races reorder admissions. A later conflicting write must not overwrite the stored winner. This closes a gap left by per-key request and signature counts: the wrong immutable payload could otherwise be paired with the right ID.

## Evidence and limits

Run `python3 -m unittest discover -s tests -p "test_gateway_*audit.py" -v` and `python3 -m unittest discover -s tests -p test_ci_evidence_gate.py -v`. The real Spring acceptance gate additionally requires PostgreSQL, RabbitMQ, the SQLite-backed external provider and the independent Java producers. Local unit-test success is not an integration result. No physical-host independence, production SLO, or unconditional exactly-once claim is made.
