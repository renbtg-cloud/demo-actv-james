# Milestone 136 — Deterministic global two-phase evidence commitments

On verified opt-in handoff, the final global verdict includes `readiness_handoff.status`, run/session identities, a canonical SHA-256 of the full reproduced preflight verdict and ordered per-shard host IDs/exact-byte readiness SHA-256 values. Reversed incoming file order yields an identical commitment. This connects audit records but does not independently establish trusted timestamps, real remote execution, hardware identity or exactly-once effects.
