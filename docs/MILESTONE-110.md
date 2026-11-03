# Milestone 110 — Preflight reports bound to the actual launch manifest

The per-host readiness record schema v2 adds a canonical SHA-256 of the exact
collector-held launch manifest and an observed wall-clock timestamp. The
independent audit refuses an otherwise plausible report copied from a different
launch plan or run ID. These fields are **self-reported metadata**, not a
cryptographic remote-host identity: an untrusted host can fabricate them.
