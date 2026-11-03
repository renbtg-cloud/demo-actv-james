# Milestone 117 — Bounded freshness for self-reported host readiness

Opt-in --strict-temporal validates each producer report against the collector time: at most 300000 ms old, at most 10000 ms into the future, and at least 30000 ms before the launch barrier. Both budgets are bounded CLI options. Producer time remains untrusted; this is a plausibility gate, never verified clock synchronization.

Verification: `python3 -m unittest discover -s simulator/tests -p "test_host_matrix*.py" -v`. The real multi-host host-provenance acceptance remains pending independently provisioned machines.
