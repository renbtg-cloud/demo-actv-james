# Milestone 111 — Complete shard-indexed readiness inventory

`simulator/host_matrix.py` requires precisely one readiness record for every
shard assigned by the trusted launch plan. Reports may arrive out of order.
Missing, duplicate, altered or unexpected shard indices and launcher hashes
are refused. The auditor also cross-checks each declared JVM process count,
aggregate heap allocation and runtime/resource information against the plan.
The readiness matrix is a preliminary **operator gate**, not an activation
success verdict.
