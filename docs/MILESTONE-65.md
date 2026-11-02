# Milestone 65 — Fail-closed terminal-state counter transitions

The observer rejects a nonzero initial sample, malformed or missing counters, clock regressions, backwards counters, DONE+FAILED exceeding admitted keys, counts above the workload's unique-key budget, unknown or FAILED final outcomes and exceeding the bounded sample budget. It requires the final aggregate to be exactly expected admitted/DONE with zero FAILED.

It reports first admitted / first DONE / all DONE observation times relative to the first baseline reading and the time between the last incomplete and first complete sample. That interval is the polling-resolution uncertainty, **not a claim of precise completion time**. The existing independent per-key PostgreSQL/SQLite/signature audits remain mandatory and prevent aggregate counts from masquerading as identity proofs.

`simulator/tests/test_terminal_progress.py` tests mutation, missing evidence and a real background sampler thread without requiring a database. Real Spring CI tests the same implementation with actual PostgreSQL, not a stand-in.
