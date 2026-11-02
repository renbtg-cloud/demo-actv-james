# Milestone 98 — Offline imported evidence to full global workload audit

A real integration fixture signs two host outputs, exports each to ZIP, imports the archives in reverse arrival order, and passes the *unchanged* distributed_merge workload and Ed25519 auditors. This proves integration of transported bytes with existing replay/identity logic, not independent physical host execution. The simulator lab CI includes the acceptance tests.

**Scope and limits:** no SSH transfer, VM provisioning, host custody, performance benchmark or hardware attestation is claimed. Receivers and incoming archives must be placed on a collector-controlled filesystem; private host keys are never packaged. The collector accepts transport and signatures first, and a separate global workload verifier decides application correctness.
