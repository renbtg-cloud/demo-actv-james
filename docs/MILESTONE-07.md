# Milestone 07 — multi-host-ready input generation, global admission audit

This remains **one integrated Deroyque project** with the service, ingress simulator and independent mock provider. The new functionality is distributed *load orchestration by deterministic shards*, not a claim that a remote Linux machine was provisioned.

## What was implemented

- `simulator/run.py`: `--shard-index`, `--shard-count`, `--start-at-ms` partition one global plan across multiple coordinators, each launching truly independent Java 21 producer JVMs. Each host receives disjoint global sequence numbers but uses the same shared idempotency-key space and run ID.
- Java `LoadAgent`: reads sparse, globally indexed plans and waits on a shared wall-clock barrier before running its locally owned HTTP POSTs. HTTP latency is measured using `System.nanoTime()` for each JVM; a shared time-base for *cross-host* concurrency is **not** assumed.
- `simulator/distributed_merge.py`: combines all shard TSV files, checks exact sequence coverage, checks common run parameters, regenerates the deterministic global plan, audits that each external key has exactly one creation, checks replay and conflicting payload behavior, and can verify logical state once using the service's read API.
- A shard that is locally healthy reports `SHARD_PASS_GLOBAL_AUDIT_PENDING` — only the merged report may report global `PASS`.
- `simulator/distributed_prepare.py`: creates N individually executable `launch-host-NNN.sh` scripts from one plan, no credentials embedded. It requires HTTPS for non-loopback URLs; a secure SSH tunnel to loopback is an option for laboratory service endpoints.
- Regression tests cover missing shards, incompatible plans, modified payload identities, duplicated sequence numbers, safe launch preparation and unsafe remote HTTP rejection.

## Real Linux findings (Java 21 / PostgreSQL 17.11 / RabbitMQ 4.4.0 / external SQLite mock)

Only **one Linux host** was available: `e1cf6267c02d`. Two *separate Python coordinator processes* launched their own independent Java producer processes against the live service, but both were on this host. The Linux container and Python tools resolved to the same environment. There was no separate provisioned Linux server; physical or cgroup isolation was **not** demonstrated.

| Scenario | Producers | POSTs | Unexpected HTTP/transport | Status 202 | 200 | 409 | Logical effects reconciled | Global audit |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Two local coordinators, 4 JVMs each | 8 | 160 | 0/160 | 40 | 76 | 44 | 40/40 `DONE` | PASS |
| Two local coordinators, 8 JVMs each | 16 | 640 | 0/640 | 128 | 280 | 232 | 128/128 `DONE` (verified after recovery interval) | PASS |
| **Total** | | **800** | **0/800** | **168** | **356** | **276** | **168/168** | **PASS** |

The 16-JVM scenario used a peak of 8 concurrently active producer JVMs per coordinator; this is **not an independently measured, cross-host instantaneous concurrency peak**. The host has 4 CPU equivalents and 4 GiB memory cgroup limit, still shared across all components. The two host-shard local rates were approximately 77.3 and 77.5 POST/s; **they must not be added and presented as an independently verified cross-host throughput benchmark**.

Observed merged request latency p95: 466.128 ms (8 JVMs), 1050.614 ms (16 JVMs). Both are ingestion HTTP latency distributions, not provider-completion latency or a server-only saturation measurement.

Automated simulator tests: **0/46 failed; 46/46 passed**. Cross-host workloads were additionally executed against actual PostgreSQL and RabbitMQ. No independent network partitions or independent host failure were tested.

## Run on genuinely separate Linux hosts

The application in this laboratory currently binds locally. **Do not expose its development bearer-token API to the public internet.** Establish an authenticated, encrypted tunnel to it on each generator host, e.g. a properly authorized SSH local port-forward, or deploy a secured TLS reverse proxy with appropriate network restrictions. Use distinct machines and independently verified time synchronization (e.g., chrony); time skew causes incorrect launch coordination and prevents meaningful cross-host in-flight overlap inference.

1. Install **the same** standalone simulator package on each generator Linux, with Java 21 and Python 3.11+ (no PyYAML needed when only CLI arguments are used). Each must be able to reach the **same** authorized API endpoint. Configure `DEROYQUE_SIM_WRITE_TOKEN` securely on each host, and `DEROYQUE_SIM_READ_TOKEN` on the host where post-run verification occurs. These environment variables must not be placed in generated scripts, published archives or command arguments.
2. On a trusted coordination machine, from the unpacked repository root, prepare the workload (choose a sufficiently distant start time to copy scripts and launch all machines):

   ```sh
   python3 simulator/distributed_prepare.py \
     --url http://127.0.0.1:18131 --shards 2 --jvms-per-host 8,8 \
     --requests 640 --keys 128 --rate 160 --conflict-percent 45 \
     --start-after-seconds 180 --output-dir launch-multi-host
   ```

   This example assumes an authenticated tunnel on **each** generator host providing the same local port. Otherwise use an HTTPS endpoint and ensure the service has been securely exposed for this purpose.
3. Securely transfer `launch-host-000.sh`, `launch-host-001.sh`, etc. to their respective hosts, with the same simulator code, and execute each at the simulator repository root before the start barrier. No remote provisioning or SSH copying occurs automatically.
4. Collect each host's complete result directory (e.g. `simulator/results/<run-id>-host-000`) to a trusted aggregation machine; keep data protected because results contain internal IDs and request identities. Verify all shards are present and run:

   ```sh
   python3 simulator/distributed_merge.py --verify-state --await-done-seconds 90 \
     simulator/results/<run-id>-host-000 \
     simulator/results/<run-id>-host-001 \
     --output final-merged.json
   ```

5. For real throughput benchmarking, use a single service-side measurement clock and server-side counters, or independently verify clock offsets and intervals. The current merger deliberately leaves `global_throughput_qps` and `global_peak_http_concurrency` **null**; per-host rates and durations remain available. A cross-host rate calculation using unverified wall clocks would be misleading.

## Candid limitations

- No second Linux machine was accessible in this session. The scripts are remote-host-ready, but remote-host execution, TLS/SSH connectivity and remote host restarts are not demonstrated.
- Input simulation uses the HTTP POST admission API. Provider completion is checked separately; admission rate is not effect-completion throughput.
- Files copied for offline aggregation are trusted as evidence of observed test execution; they are **not cryptographically attested**, and the authenticated application API is still in laboratory form.
- Clock-barrier launch is best effort; JVM startup, CPU scheduling, TCP queueing and clock skew create different actual send times. It is not a distributed hard-real-time scheduler.
- Controller memory guards work **per host**. Two controllers launched on the same host do not automatically coordinate their memory budgets.
- Next infrastructure task: acquire/provision a distinct Linux or user-controlled remote VM, securely connect it, and perform actual remote benchmarks plus service-side metrics/JFR under isolated producer load. Do not infer service capacity from these local-host figures.
