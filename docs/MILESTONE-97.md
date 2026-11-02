# Milestone 97 — Cross-host impersonation and replay rejection

Adversarial tests cover reused session evidence, swapped host assignments, changed launch manifests, deliberate re-sealing of modified TSV bytes, missing shards, duplicate archives and incomplete clock reports. Rejected collections leave no published directory. Authentication remains bounded to pinned signing keys, not hardware trust.

**Scope and limits:** no SSH transfer, VM provisioning, host custody, performance benchmark or hardware attestation is claimed. Receivers and incoming archives must be placed on a collector-controlled filesystem; private host keys are never packaged. The collector accepts transport and signatures first, and a separate global workload verifier decides application correctness.
