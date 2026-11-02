# Milestone 93 — Portable deterministic host output

Single-host evidence is transferred as a deterministic ZIP_STORED archive containing only summary.json, the exact agent-NNN.tsv set, a previously signed Ed25519 envelope and an optional signed clock report. Fixed member metadata makes two exports of the same inputs byte-identical. The SHA-256 inventory is transportation integrity only; it is not a host signature.

**Scope and limits:** no SSH transfer, VM provisioning, host custody, performance benchmark or hardware attestation is claimed. Receivers and incoming archives must be placed on a collector-controlled filesystem; private host keys are never packaged. The collector accepts transport and signatures first, and a separate global workload verifier decides application correctness.
