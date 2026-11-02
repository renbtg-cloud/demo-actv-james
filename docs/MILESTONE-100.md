# Milestone 100 — Aggregate memory/disk budget for offline collection

Incoming signed-shard ZIPs now undergo an aggregate **byte budget** before staging, followed by a second cumulative limit on the bytes actually copied into private storage. Default: 2 GiB. The Linux CLI accepts `--max-collection-bytes`; the maximum permitted configuration is 16 GiB. This is an upper bound on incoming archive bytes, not a reservation, memory cap, or guarantee of free disk space. The preflight refuses missing/excess archives; the extraction continues to enforce each ZIP member's limits.

The limit is enforced before any published collection appears. Adversarial tests include overly small budgets, negative/Boolean/out-of-range configuration and a two-shard aggregated budget. No remote transport/authentication feature is implied.
