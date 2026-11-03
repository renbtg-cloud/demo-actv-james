# Milestone 120 — Deterministic per-shard preflight commitments

Successful matrix verdicts now contain manifest_sha256 and an ordered report_commitments array with canonical_sha256 per shard. Reversing arrival order does not alter it; a well-formed changed report alters the commitment. These are hashes of self-reported documents, not evidence of their truthfulness.

Verification: `python3 -m unittest discover -s simulator/tests -p "test_host_matrix*.py" -v`. The real multi-host host-provenance acceptance remains pending independently provisioned machines.
