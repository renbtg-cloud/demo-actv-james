# Milestone 30 — Assertion-completeness gate in GitHub Actions

## Failure mode addressed

An integration script can exit 0 despite failing to execute its intended
assertions because of a future harness bug, early shortcut or stale result.
A green job or a printed PASS label alone is not sufficient acceptance evidence.

## Mechanism

The independent `scripts/ci_evidence_gate.py` reads the **specific JSON result
file from each of the nine real Spring Boot jobs**. It refuses missing/invalid
JSON, duplicate evidence keys, wrong or zero exit code, zero/incomplete coverage,
inconsistent pass/fail totals, and nonpassing/nameless assertion rows.

Minimum coverage is pinned to the **observed 2 November 2026 Milestone 27
matrix** (run 41496282564), not fabricated expectations: 44 checks for each
crash and competing-windows job, 44 for exact fault windows, 19 for dependency
interruptions, 16 for late/contradictory proof, 22 for same-row contention,
69 for JWT security, and 26 each for the two genuine Keycloak jobs.

It additionally gates proof that matters for each scenario: actual Spring Boot
and Flyway initialization and completed durable work for multi-JVM crashes;
signed independent provider effect for JWT; genuine issuer and signed effect
for Keycloak; a single effect for same-row contention; and nonempty injected
fault case evidence elsewhere. Only controlled scenario names, integer counts
and gate status are added to `GITHUB_STEP_SUMMARY`; neither raw JVM logs nor
bearer tokens nor private keys are uploaded.

## Tests

`tests/test_ci_evidence_gate.py` supplies positive examples for all nine
scenarios and negative controls for empty arrays, lying counts, missing effect
proof, invalid exit values, duplicate JSON fields and false Spring/runtime
claims. Local test invocation:

```sh
python3 -m unittest discover -s tests -p test_ci_evidence_gate.py -v
```

**Local result:** none of 11 unit checks failed (0/11); 11/11 passed.
A CI green result requires the **newly appended gate to pass in all nine
actual jobs**, not merely that these fabricated unit fixtures pass.

## Boundaries

These checks improve integrity and completeness of the existing evidence, but
do not provide cryptographic provenance of CI execution, independent auditing
of a compromised runner, or proof that reported business assertions themselves
are correctly implemented. Future intentional test redesigns must consciously
update the coverage floor with fresh evidence.
