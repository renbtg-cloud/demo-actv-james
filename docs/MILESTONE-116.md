# Milestone 116 — Manifest barrier equality gate

In optional strict temporal mode, the collector rejects a launch manifest whose top-level UTC barrier differs from its embedded launch-plan start. It also rejects an expired or under-30-second barrier relative to the trusted collector clock. Both inputs are collector-owned; the check does not authenticate producers.

Verification: `python3 -m unittest discover -s simulator/tests -p "test_host_matrix*.py" -v`. The real multi-host host-provenance acceptance remains pending independently provisioned machines.
