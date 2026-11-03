# Milestone 124 — Exact-byte readiness binding

Every signed payload commits to SHA-256 of the **exact 131072-byte-bounded report file** (not JSON semantic normalization). Any whitespace, key-order or data mutation invalidates the proof even if the parsed report would be equivalent. The collector reuses the exact bytes used for its host matrix audit to prevent signing/verifying different file reads. File-descriptor-based, nonblocking input rejects final symlinks and nonregular files.
