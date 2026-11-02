# Milestone 23 — Strict UTF-8 and Unicode-scalar admission

## Invariant and concrete change

An activation identity and its customer/product values MUST decode identically on
all interoperating systems. Previously `new String(bytes, UTF_8)` silently
replaced malformed octets with the replacement character. The small JSON parser
also allowed unpaired UTF-16 surrogate escapes; a later UTF-8 writer can replace
these, causing a database identity different from what arrived on the wire.

`AdmissionHttpFacade` now uses `CharsetDecoder` with `REPORT` for malformed and
unmappable UTF-8. `StrictJson` rejects an unpaired surrogate in **any** JSON
string (including keys), while accepting a valid high/low surrogate pair.
Malformed payloads return HTTP 400 before any durable ledger operation.

## Evidence and nonclaims

The Java core acceptance test runs without Spring or external services. It
exercises UTF-8 invalid octets, raw surrogate encodings, lone high/low escaped
surrogates, invalid pairs, and a legitimate supplementary Unicode character.
The `jwt-security` real Spring CI job additionally transmits raw invalid octets
and escapes through the actual HTTP listener and checks the PostgreSQL ledger
stays empty until the first deliberate valid request.

This enforces **well-formed Unicode**, not NFC/NFKC canonical equivalence:
visually similar strings with distinct valid code-point sequences remain distinct
idempotency identities. No claim of global uniqueness across different vendors
or languages is made.

**Validation status:** Local JDK core contract passed before commit; real Spring
verification must be checked against the new workflow run.
