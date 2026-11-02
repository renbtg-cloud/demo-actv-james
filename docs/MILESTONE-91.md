# Milestone 91 — Independently signed final collector verdict

The collector can sign the SHA-256 digest of the **exact bytes** of its final
PASS report and the corresponding challenge-session digest using an independent
Ed25519 collector private key. Only complete PASS results with empty violation,
transport and harness-error collections can obtain a receipt. The output is a
small detached signed JSON envelope; raw logs and private keys are not embedded.

A pinned collector key authenticates the collector's assertion, not truth of the
underlying data when that collector is compromised.
