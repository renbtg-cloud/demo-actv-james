# Milestone 103 — Pre-publication durability ordering

Before the existing Linux `renameat2(RENAME_NOREPLACE)` publication, the collector flushes all validated staged files and directory metadata with `fsync`. It then synchronizes the destination parent directory after rename. An injected prepublication flush failure rejects the operation and removes the staging directory; no externally visible partial result is published. Destination creation races continue to fail without overwriting previous evidence.

A rare **post-rename** directory fsync failure can leave a published path with durability not established: this is not falsely reported as a successful collection, and an operator must inspect the destination. These calls cannot guarantee against dishonest storage hardware or an administrative compromise.
