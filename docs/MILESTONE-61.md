# Milestone 61 — Observed winner rather than assumed scheduling order

## Purpose and implementation

The global, fully reconciled Java-producer report now records a sanitized mapping of each unique key slot to the payload variant which actually won admission (`winning_variants`). It is derived from the existing audit of real HTTP response statuses and durable request UUIDs, not from the planned send order. Multiple producer JVMs can reorder requests; asserting that the first *scheduled* variant must win would create an invalid benchmark. The mapping excludes customer personal data and is checked for complete exact slot coverage.

## Evidence and limits

Run `python3 -m unittest discover -s tests -p "test_gateway_*audit.py" -v` and `python3 -m unittest discover -s tests -p test_ci_evidence_gate.py -v`. The real Spring acceptance gate additionally requires PostgreSQL, RabbitMQ, the SQLite-backed external provider and the independent Java producers. Local unit-test success is not an integration result. No physical-host independence, production SLO, or unconditional exactly-once claim is made.
