# Milestone 148 — Read-time mutation detection and binding

The launcher reader compares dev/inode/size/mtime/ctime across its bounded snapshot, and the returned bytes must hash to the operator-provided launch manifest. Deterministic adversarial tests inject a changed timestamp and modified launcher payload. These checks cannot prevent a later rewrite after preflight.
