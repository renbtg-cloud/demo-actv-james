# Milestone 102 — Source provenance inventory from frozen bytes

Every accepted shard now records both the SHA-256 digest and the byte count of the **snapshotted input ZIP**. These are generated from the same opened source stream that was extracted, rather than re-reading an external path that could have been replaced. Incoming ZIP snapshots are deleted from the final published collection to avoid unnecessary duplication; to independently recheck source transfer digests after import, retain the original received ZIP files in the evaluator's trusted archive and use the independent verifier introduced in M104.

These SHA-256 fields provide integrity/correlation, not proof that the uploader or originating computer was trustworthy.
