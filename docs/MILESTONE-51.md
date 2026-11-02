# Milestone 51 — Every durable identity has its own complete request graph

## Objective

A count of 20 admissions, 20 dispatches, 20 settlements and 20 signed proofs does
not prove they belong to the **same 20 requests**. Offsetting omissions and
duplicates can preserve the totals. The real Spring gateway test now queries
PostgreSQL **per accepted workload identity**, with read-only correlated
subqueries to avoid multiplication from one-to-many joins.

It requires every expected `run-id` plus zero-based key number to occur exactly
once and to own exactly one dispatch, one settlement, one signed attestation
and one attestation bearing the expected laboratory issuer, key id, outcome,
effect count and generation. It also detects settlements that incorrectly point
to a dispatch for a *different* request. No arbitrary SQL identifiers or
untrusted string fragments are accepted; workload prefix matching uses
`left(..,length(..))` rather than wildcard-vulnerable `LIKE`.

This is a strict invariant for this **clean, fault-free gateway workload**. A
legitimate retry-heavy distributed scenario may create multiple dispatch
attempts and needs a different audit contract; this milestone does not decree
one dispatch per request for the engine's general behavior.

## Evidence

`python3 -m unittest discover -s tests -p test_gateway_identity_audit.py -v`
executes positive and mutation controls. The real `gateway-witness`,
`gateway-soak` and repeated `gateway-trials` acceptance harnesses will each
run the identity graph query against their private Flyway schema. A green CI
job is required before calling integration verified.
