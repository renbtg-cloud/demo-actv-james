# Milestone 37 — Reconcile independent service-JVM metrics without pretending clocks are shared

## Problem and engineering outcome

The existing distributed input merger could validate only one in-memory service metrics snapshot. This falsely suggests one JVM receives the entire workload when several live Spring Boot instances share the ingress load. A cluster has multiple independent monotonic clocks, so adding per-JVM QPS or peak concurrency is mathematically unjustified.

`simulator/distributed_merge.py` now accepts repeated `--server-metrics` arguments, one snapshot **for every active service JVM that received tagged traffic**. It refuses duplicated instance IDs, invalid run identity, negative/boolean counters, in-flight or incomplete responses, nonsensical local monotonic intervals, and disagreeing per-instance status totals. It requires the sum of all service HTTP statuses to match the complete producer record set.

When precisely one service receives all requests, the prior single-JVM monotonic admission-rate result is retained. With two or more JVMs the tool retains *per-JVM* rate/window/peak plus total completed admissions, while explicitly returning `global_throughput_qps: null` and `server_handler_peak_inflight: null`. It must not present those sums as globally simultaneous measurements. No external-provider completion rate is implied.

## Example after capturing all snapshots

```sh
python3 simulator/distributed_merge.py \
  --server-metrics /lab/service-a.json \
  --server-metrics /lab/service-b.json \
  /lab/shard-0 /lab/shard-1 --output /lab/merged.json
```

`server_metrics.py` retrieves an authenticated per-service snapshot; the evaluator must route requests to **each individual service process**, not repeatedly to a load-balanced address. Each instance must stay alive for its entire measurement window; restart resets the in-memory counters. A missing instance is detectable through the total counts only if traffic reached that instance; snapshots are not cryptographically signed.

## Proof scope

Automated offline tests split 12 synthetic HTTP records across two independently named service snapshots, verify individual time windows, reject missing or duplicated snapshots and tampered status counters, and ensure no global rate is fabricated. The GitHub simulator-contract job validates these cases using real Python, but this milestone does **not** claim physical multiple-host execution or a real measured multi-service benchmark. Such an experiment remains a separate next step.
