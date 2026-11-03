# Milestone 122 — Adversarial matrix regression and fail-closed CLI

Added a dedicated test suite covering temporal contradictions, stale/future reports, barrier expiry, duplicate JSON, symlink, FIFO, oversize, non-finite values, deterministic commitments and immutable verdict files. A rejected CLI run must not publish a success result. Tests use two synthetic fingerprints or same-kernel local processes, not remote independent machines.

Verification: `python3 -m unittest discover -s simulator/tests -p "test_host_matrix*.py" -v`. The real multi-host host-provenance acceptance remains pending independently provisioned machines.
