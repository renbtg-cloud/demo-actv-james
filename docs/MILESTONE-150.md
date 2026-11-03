# Milestone 150 — Concurrent publication and no-overwrite

Six independent CLI processes race to publish the same result; exactly one succeeds and five fail without replacing its bytes. Existing symlinks and their targets remain unchanged. This is local atomicity, not shared-NFS atomicity proof.
