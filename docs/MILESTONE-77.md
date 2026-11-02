# Milestone 77 — Deterministic multi-host readiness regressions

The existing GitHub Actions `simulator-lab-contracts` job runs the independent launch/index/clock regression suites before the complete simulator suite. Adversarial tests reject changed scripts, plan mismatches, tampered agent bytes, duplicate/extra evidence, symbolic links, invalid clock intervals, suspicious local wall-clock jumps, untrusted remote HTTP probes and same-host masquerading as separate physical systems. The same gate also verifies upload-order independence of shard evidence and the opt-in strict preflight.

**Scope:** all tests so far run on a laboratory Linux or GitHub-hosted runner. Successful CI does **not** mean a genuine geographically/physically separated multi-host experiment has run. The project owner will supply independently operated hosts for that separate acceptance stage. CI conclusions must be checked in the specific workflow run after publication.
