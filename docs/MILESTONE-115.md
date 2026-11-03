# Milestone 115 — Independent OS-process negative host-isolation experiment

The CI preflight matrix now launches **two separate Python operating-system
processes**, each interrogating the actual installed Java runtime and
producing a full per-shard readiness report. Both receive the same run-salted
kernel fingerprint on the hosted Linux runner. The matrix must reject this
scenario as `SAME_KERNEL_OBSERVED`. Artificially distinct fingerprints are
covered only as separately labelled self-report fixtures. Thus CI can detect
an important false multi-host claim without pretending a single CI runner
provides physically independent machines.
