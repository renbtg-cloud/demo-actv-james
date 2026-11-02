# Milestone 64 — Independent monotonic PostgreSQL terminal observer

The actual Spring gateway load starts a separate read-only sampler *before any producer requests*. On a dedicated Python thread, it polls an aggregate SQL query for admitted, DONE and FAILED request counts, from the temporary schema and only the current run. It records no credentials or business payloads. Sampling uses monotonic timestamps **after** each database read, so blocking query time is not falsely recorded as an earlier observation. The baseline must be 0/0/0.

The observer uses an interval of 0.5 seconds for short loads and 1.0 second for the two-minute experiment. The sample budget is bounded. Every unhandled sampling error propagates back to the real-infrastructure harness. CI cannot pass if this trace is missing or reports fewer than the expected terminal identities. See `simulator/terminal_progress.py` and `tests/run_spring_gateway_witness.py`.

**Meaning:** this measures when terminal state becomes *visible in PostgreSQL* under one observer clock. It does not measure the exact time of the provider's side effect or per-request latency. The query itself consumes resources and may affect the measured workload.
