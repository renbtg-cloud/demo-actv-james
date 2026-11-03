# Milestone 139 — Independent collector preflight signing domain

Added `simulator/readiness_checkpoint.py`, which signs the collector's stored, fully audited and host-signed readiness matrix with a **separate collector-held Ed25519 key** using domain `deroyque-readiness-checkpoint-v1\0`. The collector key is not a host-producer key and should be independently enrolled by the evaluator. This signature proves control of the enrolled collector key, not when the historical data was created or whether any remote computer truly ran.
