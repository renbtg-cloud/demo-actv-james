# Milestone 63 — Second-language SHA-256 fingerprint and CI acceptance

## Purpose and implementation

Recompute the exact Java `PublicRequests.fingerprint` format in independent Python: `public:` plus SHA-256 of UTF-8 `(customerId + NUL + productCode)`. Reject missing/duplicate keys, inconsistent customer/product, wrong digest, invalid variant mapping and alternate noncanonical slot spellings. The real gateway short, 120-second and three-trial jobs now require the explicit `payload_contract_verified` flag; a fabricated green aggregate without it fails the CI evidence gate. Mutation tests verify the negative cases. This is a lab deterministic-payload proof, not a production general data-integrity certification.

## Evidence and limits

Run `python3 -m unittest discover -s tests -p "test_gateway_*audit.py" -v` and `python3 -m unittest discover -s tests -p test_ci_evidence_gate.py -v`. The real Spring acceptance gate additionally requires PostgreSQL, RabbitMQ, the SQLite-backed external provider and the independent Java producers. Local unit-test success is not an integration result. No physical-host independence, production SLO, or unconditional exactly-once claim is made.
