# Milestone 99 — Signed kernel-collision gate at collection time

The trusted collector now includes the already available host-provenance
classification in its transported, signed collection summary. With explicit
`--require-distinct-kernels`, it refuses missing fingerprints or duplicate
run-salted kernel boot IDs **before publication**. Mutated test fixtures are
re-signed with genuine Ed25519 private keys to demonstrate that a valid
signature does not turn two roles sharing a kernel into independent fault
domains. The default remains descriptive, preserving legitimate same-host
integration tests without claiming host isolation.

Distinct *self-reported* kernel hashes are labelled
`DISTINCT_KERNEL_REPORTS_UNVERIFIED` and **cannot prove separate physical hosts**;
physical and administrative independence must be established outside these
software records. This milestone changes no production activation service code.
