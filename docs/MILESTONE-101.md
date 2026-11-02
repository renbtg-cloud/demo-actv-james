# Milestone 101 — File-descriptor-pinned hostile-archive snapshot

After a preliminary ZIP inspection, collection opens each external source via `O_NOFOLLOW|O_NONBLOCK`, validates its `fstat` regular-file/link-count/size, and streams a bounded snapshot to a private staging directory. Only the snapshot is inspected again, extracted and hashed into the collection inventory. Changes to its reported shard identity across the boundary fail closed. This removes reliance on the external pathname remaining stable through validation and extraction. FIFO/symlink/hardlink and replacement races have adversarial regression coverage.

The snapshot is not host authentication, and an attacker who possesses a valid signing key remains outside this claim. Signed evidence is still independently validated against the trusted collector session.
