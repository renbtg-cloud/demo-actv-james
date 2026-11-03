# Milestone 146 — Independent evaluator checkpoint runbook

The operator protocol now explains generating a separate owner-only collector signing key, preserving and signing the verified exact preflight matrix, authenticating the collector public key out of band, verifying the detached checkpoint, and supplying both checkpoint and enrolled key during the final merge. The signature does **not** establish trustworthy chronology, truthful host provenance, independent hardware, production-grade performance or success of external provider effects.
