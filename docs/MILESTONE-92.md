# Milestone 92 — Offline third-party receipt verifier and mutation tests

The receipt verifier requires a separately pinned collector public key, the
unaltered final report, the collector challenge and the receipt. It verifies
the Ed25519 signature, original result bytes, session digest and exact
schema/claim. The test suite alters whitespace, report fields, signing keys,
session identity and signature bits; each mutation is rejected. A subprocess
CLI round-trip and refuse-to-overwrite contract are also tested.

This is transportable evidence of a collector verdict, not universal
exactly-once semantics, independent physical hardware or legal nonrepudiation.
