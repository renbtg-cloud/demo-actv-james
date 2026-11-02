# Milestone 43 — Fail-closed CI witness evidence gate

The new `gateway-witness` GitHub Actions scenario is part of the full integration matrix and has its own **minimum 16 independent assertions** evidence contract. `scripts/ci_evidence_gate.py` rejects missing/contradictory assertion lists, unsuccessful process exit, absent 80-record global reconciliation, missing 20 terminal states, wrong backend count, or absent gateway completion rate. It summarizes assertion counts, never credentials or raw application logs.

The offline test exercises forged and incomplete evidence alongside the previous nine scenarios. The CI's automated verdict is only as trustworthy as its checked assertions and genuine execution; green code compilation or a Java/Python stand-in is not a substitute for a green real Spring scenario.
