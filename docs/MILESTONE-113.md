# Milestone 113 — Linux-ready cross-shard audit regression

The hosted CI job now tests both the real Linux per-host preflight and a new
fail-closed matrix auditor against two generated shard launchers, real Java
runtime/version checks, replayed launch plans, colliding kernel IDs, lost shard
coverage, incorrect resources and explicit network requirements. The CLI is
executed as a separate Python process with real filesystem reports. Synthetic
distinct fingerprints in the positive fixture are **not** represented as actual
independent hosts: the real single-kernel fixture explicitly fails the isolation
check. This is preparation for the user's eventual real multi-host experiment.
