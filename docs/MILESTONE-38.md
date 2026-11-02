# Milestone 38 — Producer-host kernel provenance and explicit distinct-kernel gate

## Problem and implementation

Two simulator coordinators may run on the same Linux machine, even if described as "distributed". A shared kernel boot ID is a measurable indication that the producers are NOT running separate kernels. The simulator now embeds bounded `host_environment` self-report metadata in every shard summary: kernel release, CPU affinity count, cgroup CPU/memory limits, and a per-run salted SHA-256 fingerprint of the kernel boot UUID. The underlying boot UUID, host name and machine ID are **not** written to the report.

The distributed merger returns one of `SAME_KERNEL_OBSERVED`, `DISTINCT_KERNEL_REPORTS_UNVERIFIED`, `UNKNOWN_*`, or `INSUFFICIENT_SHARDS_FOR_KERNEL_COMPARISON`. It never silently infers two different physical computers. For experiments specifically requiring distinct observed kernels, add `--require-distinct-kernels`; the merger fails closed when the reported evidence is missing, identical, or there is only one shard.

```sh
python3 simulator/distributed_merge.py --require-distinct-kernels \
  /lab/shard-0 /lab/shard-1 --output /lab/merged.json
```

Distinct kernel boot fingerprints **do not** prove physically separate hosts, independent power/network domains, trustworthy remote hosts, or synchronized clocks. Attackers can forge both summaries; this is reproducibility metadata and an *honest negative control*, not remote host attestation. Kernel fingerprints are different for different run IDs and are not intended for tracking machines across workloads. The optional gate is not enabled for the intentionally same-host two-coordinator regression.

## Testing

The test suite checks same-kernel positive detection, missing fingerprints, insufficient shard count, distinct self-reports, opt-in fail-closed behavior and per-run pseudonymization. It validates the interpretation of evidence; genuinely separate-host deployment remains unverified in this milestone.
