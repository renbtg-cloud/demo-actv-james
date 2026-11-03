# Milestone 112 — Negative same-kernel proof and opt-in network evidence gate

When two readiness reports expose the same run-salted kernel boot identifier,
`host_matrix.py` rejects the purported independent-host layout. Different
self-reported fingerprints yield `APPARENT_READY_DISTINCT_KERNEL_REPORTS_UNVERIFIED`,
not proof of physical independence. With `--require-network`, reports without an
actual TCP/TLS probe are rejected. Even a positive TLS probe proves neither
endpoint authorization nor successful application requests. Cross-host wall
clock values are not compared without separate clock synchronization evidence.
