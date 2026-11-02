# Milestone 24 — Explicit JSON charset/media-type contract

## Invariant and implementation

The bounded public admission endpoint consumes UTF-8 JSON only. Previously it
accepted `application/json; charset=ISO-8859-1` but decoded its bytes as UTF-8,
allowing a client/server representation mismatch.

`AdmissionHttpFacade.utf8JsonContentType` now accepts `application/json`,
optionally with one `charset=UTF-8` parameter (case-insensitive; quoted value
supported), and valid unrelated `name=value` media parameters. It rejects
non-UTF-8 charset declarations, duplicate charset parameters, empty/malformed
parameters, and non-JSON media types with HTTP 415. The 4096-byte cap and
existing HTTP statuses are unchanged.

Java core tests cover accepted and rejected variants; the real Spring HTTP
harness sends raw non-UTF-8 and conflicting charset headers and verifies no
ledger writes. This is **not** a general-purpose RFC 9110 media-type parser:
complex quoted parameters containing semicolons are not supported; production
API contract should explicitly document the accepted form. JSON `charset`
handling is an application-level defensive choice, not a new JSON standard.

**Validation status:** Local JDK core contract passed before commit; real Spring
verification must be checked against the new workflow run.
