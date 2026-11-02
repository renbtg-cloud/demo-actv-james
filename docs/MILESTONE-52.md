# Milestone 52 — Independent provider effect by exact identity

## Objective

An isolated SQLite supplier with N `effect` rows can still contain the wrong
keys, duplicated generations or rejected outcomes. The gateway harness now
opens the supplier's database **read-only** and checks the exact set of
expected external IDs, requiring precisely one `generation=1` and
`outcome=ACTIVATED` effect per expected key. It rejects extraneous keys even
if they offset missing keys in aggregate counts.

This supplements, rather than replaces, the earlier PostgreSQL signed receipt
and completed-state checks. It proves properties of a disposable simulated
supplier under the recorded test conditions, not an arbitrary commercial
supplier's execution semantics.

## Negative controls

The local suite injects missing, swapped, repeated-generation and incorrectly
rejected effects with identical-looking totals. The evidence gate also refuses
absence of the per-key provider verification flag.
