# Milestone 125 — Collector challenge and enrolled host/shard role binding

Signing validates collector `session.json` against its retained launch manifest and pinned Ed25519 public keys, then binds `run_id`, `host_id`, `shard_index`, manifest identity and the SHA-256 of the fresh session. An incorrect private key, swapped host assignment, changed launch manifest or replay across collector sessions fails. No signer-supplied pin list may substitute for collector-held enrollment.
