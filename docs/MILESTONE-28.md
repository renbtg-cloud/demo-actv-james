# Milestone 28 — Deterministic Unicode parser property campaign

## Reason

The deliberately minimal `StrictJson` parser is used to protect identity
fields and idempotency, but a few handpicked vectors are not enough to test
escaped-vs-literal Unicode equivalence or surrogate handling systematically.

## Test construction and scope

A reproducible Java 21 runner (`StrictJsonUnicodePropertyTest`) uses a fixed
seed, 20261102, to generate 1,000 strings made from independently specified
Unicode scalar values including supplementary characters, controls, quotes,
backslashes, combining accents and near-maximum Unicode points. It constructs
both raw-UTF-8-source and fully escaped Unicode representations, then checks:

- The raw representation round-trips to exactly the expected Java string.
- The escaped representation round-trips to exactly the expected string.
- Both representations map to the same object and identity.

It then verifies 512 lone-surrogate escapes fail, plus 100 escaped duplicate
object-key aliases fail, for a total of 3,612 assertions. This deterministic
campaign runs in-process without Spring, PostgreSQL or the mock provider; it is
also added to the real Spring `jwt-security` CI job as a precondition.

## Evidence and limitations

The Java 21 local run reported **0/3612 failures; 3612/3612 passed**. The
independently executed HTTP/Spring regression remains separate and must still
pass CI. Since both comparison representations originate from our test
serializer, this is a property test, **not** differential comparison against a
third-party standards-compliant parser. It does not validate arbitrary JSON
objects, numeric syntax, canonical Unicode normalization, or corpus-wide
property-based security claims.
