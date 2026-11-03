# Milestone 118 — Reject ambiguous or non-finite host JSON

Untrusted JSON readiness reports now reject duplicate object keys at every depth, plus NaN and Infinity. Ambiguous metadata can no longer silently pick the last occurrence during collector interpretation. Trusted collector manifest parsing is unchanged.

Verification: `python3 -m unittest discover -s simulator/tests -p "test_host_matrix*.py" -v`. The real multi-host host-provenance acceptance remains pending independently provisioned machines.
