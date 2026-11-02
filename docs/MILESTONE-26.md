# Milestone 26 — Cross-encoding Unicode idempotency in the real provider flow

## Aim

An identical Unicode scalar may arrive encoded as literal UTF-8 or as JSON
escaped surrogate pairs. These are **two JSON representations of the same
string**, not distinct business identities. Verify that one new identity
produces a single durable admission and one independently recorded effect,
including the reply sent after an idempotent replay.

## Verification design

The dependency-free Java contract first admits an escaped supplementary scalar
(`\uD83D\uDE80`) then sends the same scalar literally in UTF-8. Exactly
one creation and one replay are required. It also rejects a duplicate JSON
object key whose spelling is disguised with an escape (`exter\u006ealId`).

The actual `jwt-security` CI integration test repeats this at the Spring HTTP
listener with a valid JWT. It requires HTTP 202 for the escaped form, HTTP 200
and the **same UUID** for literal UTF-8 (including quoted charset), exactly two
distinct ledger records counting the earlier control request, final `DONE`,
exactly one independent SQLite provider effect for the Unicode key and two
persisted signed attestation rows. An escaped duplicate object key must be
HTTP 400 without adding a third ledger record.

This does **not** normalize different Unicode codepoint sequences to NFC or
NFD, imply cross-language canonical equivalence, or promise unconditional
exactly-once execution outside the tested provider idempotency contract.

**Validation status:** Java core tests must pass locally and genuine Spring
CI must pass before claiming acceptance evidence.
