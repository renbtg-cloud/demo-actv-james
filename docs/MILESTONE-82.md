# Milestone 82 — Tamper, substitution, replay and missing evidence controls

Independent regression fixtures use two distinct generated Ed25519 keys.
The collector rejects changed agent bytes, changed summary bytes, altered
signatures, invalid base64, replayed run IDs, swapped public keys, duplicate
keys and reused host identities. Reports and shard folders may arrive in
reverse order; their declared signed shard indices are canonicalized.
A false claim of physical isolation cannot be established by signing alone.
