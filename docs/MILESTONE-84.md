# Milestone 84 — Collector-pinned run contract

A new collector-held signing session binds every signed shard to the exact
schema-v2 launch manifest and the operator-enrolled public-key inventory using
canonical SHA-256 digests. Verification checks these bindings before accepting
Ed25519 evidence. The session and public-key inventory must be retained outside
the incoming host evidence bundle and supplied independently.

The contract alone is not an attestation of hardware, software-image provenance,
or remote administrative ownership.
