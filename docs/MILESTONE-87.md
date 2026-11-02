# Milestone 87 — Ed25519 attachment for per-host clock evidence

An optional clock report can be included in a session-bound host signature as
the SHA-256 of its exact serialized bytes. The collector checks that all signed
clock reports are complete, unique by shard, match the run and are unchanged.
Missing, duplicated, swapped, malformed or modified reports fail closed.

The signature authenticates a pinned signing key, not a trusted time authority.
