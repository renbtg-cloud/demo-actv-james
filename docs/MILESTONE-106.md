# Milestone 106 — Executable launch provenance and barrier preflight

`simulator/host_readiness.py` validates a specific shard's actual launcher
against its SHA-256 digest in the collector-generated launch manifest, verifies
its assigned JVM count and requires at least 30 seconds of remaining lead time
before the shared launch barrier. The preflight does not modify the workload,
start a process or attempt to authenticate the machine. Wrong-shard launchers,
modified scripts and expired barriers fail before the experiment begins.
