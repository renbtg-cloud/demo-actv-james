# Milestone 81 — Separate Ed25519 signing key per producer shard

A host signs the exact SHA-256 digest of its `summary.json` and each expected
Java-producer TSV file, together with the run ID, declared host identity and
shard index. The signature uses a domain-separated, deterministic JSON payload
and is checked against the collector's separately pinned Ed25519 public key.
Private keys are operator-owned and never distributed with source ZIPs or CI
artifacts. Unsafe key permissions, malformed input and missing agent files
fail closed. This is cryptographic **key provenance**, not machine identity.
