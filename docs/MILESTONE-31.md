# Milestone 31 — Reproducible Git source ZIP in GitHub Actions

## Purpose

Manually created ZIP deliverables can accidentally include local files,
credentials, or mixed project snapshots. The earlier SHA-256 manifests verify
archive contents but do not establish which files were selected. A Git-aware
build should deterministically select exactly the tracked project files and
publish a verified source ZIP in CI.

## Implementation

`scripts/package_source.py` invokes `git ls-files --cached -z` on the checkout,
rejects unsafe names, symlinks, missing source files, or empty selections, then
builds a ZIP with stable entry order, normalized ZIP timestamps and POSIX file
modes, and an SHA-256 manifest (`MANIFEST-SHA256.txt` plus JSON metadata).
Verification rejects mismatched hashes, missing or extra archive members,
duplicate names, unsafe traversal paths and an altered manifest. The archive
is explicitly unsigned; checksums detect accidental modification but do not
prove who produced it or protect against an attacker replacing all artifacts.

A separate `source-archive` GitHub Actions job runs five offline regression
tests (real temporary Git repositories, tampered ZIPs, output-path refusal and
byte reproducibility), packages the current tracked checkout, reopens and
verifies the ZIP, and uploads `deroyque-verified-source` as a short-retention
workflow artifact. No local `.local` state, untracked secrets or temporary
SQLite files can be selected by its Git-index enumeration. Deliberately
committed test signing keys remain present as the test-only fixtures; this
mechanism does **not** scan all committed files for secrets.

**Local result:** 0/5 offline packaging tests failed; 5/5 passed. The complete
repository ZIP publication must be verified in the **new GitHub Actions job**.
This source archive is separate from the manually generated milestone ZIPs.
