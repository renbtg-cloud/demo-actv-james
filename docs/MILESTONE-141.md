# Milestone 141 — Evaluator-enrolled collector public-key verification

The checkpoint verifier accepts a **separately pinned** PEM Ed25519 public key file; no public key embedded in untrusted matrix/receipt material may replace it. A foreign key, non-Ed25519 key, malformed base64 or modified signature fails. The public key must be authenticated in an out-of-band operator enrollment process.
