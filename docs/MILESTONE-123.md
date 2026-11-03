# Milestone 123 — Dedicated Ed25519 readiness-proof domain

Implemented `simulator/host_readiness_signature.py` with a new `deroyque-readiness-proof-v1\0` signature domain separate from post-run shard proof signatures. Each producer signs its readiness statement with its own enrolled Ed25519 key. The collector does not derive trust from self-presented public keys.

Proof of key possession is **not** an attestation of independent hardware, host administration, or honest kernel metadata.
