# Milestone 40 — Independent-clock throughput reconciliation

`simulator/witness_audit.py` compares a final gateway snapshot against all recorded producer POST responses. It validates schema, run identity, count consistency, status-class equality, per-backend counts, exact completed-request totals, absence of witness transport errors and local rejections, peak in-flight bounds, and plausible ordering of one monotonic clock's first-forward/last-completion timestamps.

`simulator/distributed_merge.py --gateway-witness FILE` reports a **gateway HTTP completion rate** only if all independent evidence reconciles; otherwise the merge outcome fails. The traditional per-service monotonic metrics remain separate, and their peak concurrency/QPS values are never summed. If both the gateway and per-JVM snapshots are supplied, both are independently checked against the complete producer evidence.

No witness record is an authenticated or signed attestation. A malicious operator could falsify the gateway and producer data together. There is no claim that source code and independent runners provide a cryptographic end-to-end proof of hardware identity.
