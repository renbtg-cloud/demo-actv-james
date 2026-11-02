# Milestone 32 — Independently verify uploaded GitHub source artifact

## Motivation

A ZIP can pass its own SHA-256 manifest verification even if the archive and
the manifest have been replaced together, or if the packaged source did not
actually match the source checkout. The M31 artifact producer and its
self-check alone do not establish a cross-run correspondence.

## Implementation

A `verify-published-source` GitHub Actions job depends on the successful
`source-archive` job, checks out the **same workflow commit on a fresh runner**,
downloads `deroyque-verified-source` via `actions/download-artifact@v4`, then
invokes `package_source.py --verify-checkout`. The additional verifier repeats
full ZIP/manifest integrity checks, enumerates tracked files from the
consumer's own Git index, requires exactly the same paths and compares the
SHA-256 of **every content byte** to the consumer's independently checked-out
worktree. Any mismatch fails the job.

Two new local adversarial unit checks verify that an untouched ZIP matches its
source worktree, and that changing a tracked file after packaging is detected
**even though the ZIP's internal hash manifest still self-verifies**.
The expanded local suite passed: **0/7 failures; 7/7 passed**.

## Guarantees and limitations

This detects accidental wrong uploads, mixed checkouts and an internally
consistent archive that differs from the Git checkout on another runner.
It does not offer external cryptographic attestation of the runner or GitHub
operator, immutable release provenance, a code-signing certificate, or
protection against an adversary able to modify *both* the source commit and
CI workflow. The real gate is the two new GitHub jobs completing successfully.
