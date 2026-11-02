# Milestone 34 — Committed-snapshot-only source packaging

## What changed

The producer now refuses any staged or unstaged tracked changes. It requires a valid Git `HEAD` and compares the Git index and working-file executable modes. It also rejects unsafe filename control characters (including line breaks, which would make the human-readable manifest ambiguous), symlinks and other nonregular tracked entries. All relevant bytes are hashed using bounded streaming reads. After generating the ZIP, the tool rechecks the checkout and independently compares every ZIP member against that same checkout.

This closes a provenance ambiguity: previously, selecting names from the Git index but reading modified working-file bytes could produce an internally consistent archive that never existed in a committed revision. Tests cover staged edits, unstaged edits, intentionally untracked secret files, newline-containing filenames and post-packaging changes.

## Caveats

The clean-check is not a transactional Git filesystem snapshot; a hostile process modifying files during an archive operation is not eliminated by pre/post tests. GitHub CI uses an isolated immutable-ish checkout, not a trusted OS attestation. Untracked files intentionally remain excluded, not treated as a reason for failure, so local packaging should occur in an isolated directory when adversarial local writers are a concern.
