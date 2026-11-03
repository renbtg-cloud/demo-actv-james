# Milestone 128 — Independent signed readiness inventory in matrix verdict

For opt-in signed audits, the no-overwrite, fsynced matrix verdict includes `signed_readiness.status`, the collector session SHA-256 and a deterministic shard-ordered list of verified host IDs, exact-byte readiness SHA-256 commitments and signed payload SHA-256 commitments. Reversed file arrival order does not change the inventory. This proves only valid signatures against the pins and byte commitments, **never** physical independence or the veracity of self-reports.
