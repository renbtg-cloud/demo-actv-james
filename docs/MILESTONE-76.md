# Milestone 76 — Canonical shard identities and strict collector requirements

The collector canonicalizes shard evidence by its declared `shard_index` rather than the order in which directory paths were passed on the command line. Duplicate, missing or contradictory shard identities fail instead of accidentally reassigning the collector's SHA-256 evidence index. Tests explicitly reverse directory input order, mutate a shard index and verify that the index remains bound to the correct producer.

The `--strict-external-preflight` switch requires a retained launch manifest, a separately held SHA-256 evidence index, clock-offset evidence and `--require-distinct-kernels`. A successful gate is still only **self-reported distinct kernel and collected evidence**; without independently authenticated machine identity it does not prove independent hardware or separate physical failure domains.
