# Milestone 96 — Linux atomic no-overwrite publication

The collector constructs an unpublished staging directory, validates all shards, then uses Linux renameat2(RENAME_NOREPLACE) to publish in a single atomic directory rename. Missing kernel support or a destination race rejects publication rather than overwriting prior evidence. The test injects a competing destination creation.

**Scope and limits:** no SSH transfer, VM provisioning, host custody, performance benchmark or hardware attestation is claimed. Receivers and incoming archives must be placed on a collector-controlled filesystem; private host keys are never packaged. The collector accepts transport and signatures first, and a separate global workload verifier decides application correctness.
