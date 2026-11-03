# Milestone 144 — Owner-only, bounded checkpoint I/O

Receipt signing requires a regular, owner-only collector private key and writes its receipt through the existing `0600` fsynced no-replace atomic publication helper. Verification bounds the receipt at 16 KiB, matrix at 2 MiB and public-key PEM at 128 KiB, refuses final symlinks, FIFOs, duplicate JSON object keys and malformed Ed25519 signatures, and does not execute incoming content. Parent directory custody remains the trusted evaluator's responsibility.
