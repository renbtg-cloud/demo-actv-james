# Milestone 95 — Session-bound signed collector import

Offline collection requires the original collector-held launch manifest, unique session challenge and pinned Ed25519 public keys. Imported shards are reordered by signed shard identity and checked for exact launch plan, host assignment, summary, TSV bytes and optional signed clock report. Transport hashes alone never authorize acceptance.

**Scope and limits:** no SSH transfer, VM provisioning, host custody, performance benchmark or hardware attestation is claimed. Receivers and incoming archives must be placed on a collector-controlled filesystem; private host keys are never packaged. The collector accepts transport and signatures first, and a separate global workload verifier decides application correctness.
