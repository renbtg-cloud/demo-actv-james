# Milestone 109 — Operator-ready JSON report and independent CI

A successful preflight optionally creates a new owner-only JSON file using
exclusive create, including real runtime versions, resource measurements,
self-reported salted kernel fingerprint, exact launch script digest and whether
network connectivity was actually checked. Existing reports are not replaced.
Regression coverage executes Java 21 and Python from a real Linux CI runner,
launches the CLI in a separate child process and simulates wrong/modified
launchers, insufficient memory and disk, unsafe URLs and barrier expiry.

A readiness report must be read as *self-reported evidence*, not signed host
identity, physical independence, trustworthy clock synchronization, or proof of
finished application transactions. The signed host envelopes and collector
remain separate prerequisites after a workload has actually run.
