# Milestone 94 — Defensive hostile-archive admission

Before extraction, the offline verifier rejects absolute and traversal names, unexpected entries, duplicates, compressed members, ZIP symlinks, malformed run/shard identity, oversized contents and SHA-256 mismatches. No archive-supplied path is used as a filesystem path. The test suite mutates real ZIP central-directory entries and payload bytes.

**Scope and limits:** no SSH transfer, VM provisioning, host custody, performance benchmark or hardware attestation is claimed. Receivers and incoming archives must be placed on a collector-controlled filesystem; private host keys are never packaged. The collector accepts transport and signatures first, and a separate global workload verifier decides application correctness.
