# Milestone 114 — Real multi-host operator protocol with audited stop conditions

A stepwise executable protocol now connects existing production-code artifacts:
launch manifest, per-shard script, collector challenge, pinned Ed25519 keys,
real Linux/Java preflight, optional TLS and bounded clock probe, independent
JVM executions, signed envelopes, hostile ZIP transport, non-overwriting
collection, offline reverification and global workload audit. The runbook
separates private credentials from public evidence and explicitly marks
unexecuted/unknown phases. It is a deployment protocol, **not** evidence
that separate physical hosts have been provisioned or exercised.
See `docs/REAL-MULTIHOST-PROTOCOL.md`.
