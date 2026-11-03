# Milestone 147 — Bounded launcher snapshots

Producer preflight reads each assigned launch script from a pinned O_NOFOLLOW/O_NONBLOCK descriptor, rejecting nonregular, empty or oversized (>64 KiB) inputs before hashing. Real FIFO/symlink tests refuse unsafe inputs without hanging. No trust in an attacker-controlled parent directory is implied.
