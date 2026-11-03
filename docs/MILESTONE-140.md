# Milestone 140 — Exact checkpoint byte digest and session binding

A checkpoint contains SHA-256 of the exact saved preflight JSON bytes, manifest SHA-256 and the validated collector-session digest. Whitespace edits, changed session challenges and substituted manifests invalidate the receipt. Payload fields are signed with a dedicated Ed25519 namespace. The verifier checks the full original reports and host proof set independently before trusting the checkpoint claim.
