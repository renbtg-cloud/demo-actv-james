# Milestone 160 — No-replace Linux directory publication

The plan producer reuses an isolated no-cryptography `renameat2(RENAME_NOREPLACE)` helper. Existing destination folders or symlinks cannot be replaced; tests check sentinel files are unchanged. This does not establish remote NFS atomicity or durability after sudden host power loss.
