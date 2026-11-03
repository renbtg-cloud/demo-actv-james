# Milestone 133 — Bind signed readiness and signed completion to identical host assignments

Optional `distributed_merge.py --readiness-matrix ... --readiness-report ... --signed-readiness ...` now requires a complete previously signed readiness proof set together with the existing post-run signed shard evidence, collector session, launch manifest and pinned keys. Host IDs, assigned shard indices and session SHA-256 must agree *across both phases*. An unsigned fallback never silently satisfies the gate.
