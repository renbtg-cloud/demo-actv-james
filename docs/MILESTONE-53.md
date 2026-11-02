# Milestone 53 — Independent Ed25519 audit of persisted evidence

## Objective

The Java service already validates supplier-signed lookup attestations.
This milestone independently retrieves **each persisted token** for the test
run and verifies its actual Ed25519 signature in Python with the public key of
the laboratory issuer, then binds the signed identity, nonce, timestamp,
outcome and generation to each PostgreSQL request and stored proof metadata.
It rejects malformed/noncanonical base64url and wrong-key/wrong-nonce/signed
claims, in addition to a separate check of the per-request cardinality graph.

Only a count of successes is placed in the sanitized CI evidence file. Raw
tokens, private keys, JVM logs and database records must not enter published
artifacts. The verification is deliberately against **test fixtures**, and
Ed25519 authenticates the provider's statement, not its factual truth.

## Gates

Run `python3 -m unittest discover -s tests -p test_gateway_signature_audit.py -v`.
The genuine gateway CI scenarios fail if the independently verified signed
proof set is not complete; the evidence gate requires the resulting explicit
boolean for both short and 120-second workloads. The three-trial harness
rechecks it for every isolated trial.
