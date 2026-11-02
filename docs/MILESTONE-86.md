# Milestone 86 — Enforced one-to-one host/shard assignments

The offline session contains one host identity for each ordered shard, and each
identity must correspond to one unique pinned public key. Unlike signatures
alone, a valid signature from host A over shard B is rejected if the session
assigned shard B to host B. Shard report arrival order remains irrelevant.

This binds key identities to expected workload roles; it cannot prove that the
actual signing private keys live on distinct physical hosts.
