# Deroyque — Integrated Milestone 06: CPU placement and deeper JFR evidence

This milestone advances the **same** service / ingress simulator / independent provider system. The new capability is controlled **CPU affinity**, NOT a claim of physical or cgroup isolation. It is designed to reduce one source of interference while preserving falsifiable performance evidence.

## Implementation

- `simulator/cpu_placement.py`: validate CPU lists against the kernel's effective affinity; inspect actual CPU affinity of each JVM and the service's OS threads.
- `simulator/run.py --producer-cpus 0,1`: spawn actual independent producer JVMs under `taskset -c 0,1`; CLI or YAML setting. The Python coordinator itself is not a load generator.
- `scripts/dev_demo.py`: optional `DEROYQUE_SERVICE_CPUS=2,3,4` and `DEROYQUE_PROVIDER_CPUS=2,3,4` apply CPU affinity **at launch**, so newly created carrier threads inherit it. Defaults are unchanged. PostgreSQL and RabbitMQ continue to share the host.
- `simulator/saturation.py --expected-service-cpus 2,3,4`: fail fast if the service leader **or any OS thread** has a different affinity; reject overlap with the configured producer mask, to avoid a misleading controlled-partition comparison. Report producer mask observations, service thread mask checks and CPU consumption.
- `simulator/thread_diagnostics.py`: generate a focused JFR settings file from JDK 21 `profile.jfc`, enabling `jdk.VirtualThreadPinned` with threshold 0 ms and stack traces; monitor-enter threshold 5 ms and thread-park threshold 10 ms. Capture is bounded per stage, and reports pinning duration buckets and the longest available stacks. A signed proof of pinning cause is **not** obtained from shallow frames.
- Preserve original run reports, JFR recordings, thread dumps, exact JVM count overlaps, schedule lag, and unexpected HTTP statuses. Never interpret planned load as achieved rate.

## Evidence from real Linux, Java 21, PostgreSQL 17.11, RabbitMQ 4.4.0, SQLite provider

CPU allowance: host affinity CPUs 0–4, cgroup `cpu.max` quota of four CPU-equivalents, cgroup memory limit of 4 GiB. All processes still share the memory and CPU quota. The service was started with CPU affinity 2–4; all its checked OS threads had the expected mask.

| Trial | Producer CPU mask | Real concurrent JVMs | POST attempts | Unexpected HTTP/transport | Target POST/s | Achieved POST/s | HTTP p95 | Scheduling-lag p95 | Verdict |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| Baseline | 0–4 | 8 | 350 | 0/350 | 70 | 69.679 | 787.603 ms | 81.501 ms | Target sustained |
| Partitioned | 0–1 | 8 | 350 | 0/350 | 70 | 69.763 | 606.692 ms | 264.084 ms | Target sustained with lag increase |
| Higher pressure | 0–1 | 18 | 720 | 0/720 | 160 | 128.824 | 1904.258 ms | 2273.421 ms | Generator could not meet schedule/shared contention |

**Total complete HTTP runs:** 0/1,420 unexpected HTTP/transport errors; 1,420/1,420 requests matched expected status classes. Every completed workload was marked PASS by its own idempotency auditor. The number 1,420 represents HTTP attempts, not independent logical assertions or externally completed provider activations.

Other attempted stages:
- 10 JVMs after the initial 8-JVM baseline were refused by the memory guard before sending work.
- 20 JVMs in the later attempt were refused by the memory guard before creating a stage; no user traffic was sent.
- **18 JVMs** is the highest count actually observed with overlapping HTTP in this milestone; it is not the maximum possible number on other hosts or under different environmental conditions.

### Diagnostic interpretation

| Trial | Pinning events | Sum of durations | Events under 1 ms | 1–10 ms | 10–100 ms | ≥100 ms |
|---|---:|---:|---:|---:|---:|---:|---:|
| Baseline, 8 JVMs | 14 | 13.638 ms | 7 | 7 | 0 | 0 |
| Partitioned, 8 JVMs | 10 | 197.467 ms | 3 | 4 | 3 | 0 |
| Partitioned, 18 JVMs | 15 | 507.053 ms | 2 | 4 | 7 | 2 |

The same service can produce very different pinning durations in short runs. Fewer events do **not** imply less time pinned. Event settings are now different from the Milestone 05 profile, so comparisons of raw pinning counts across milestones are invalid. Stacks are mainly JDK-level frames and do not prove which library or application code caused pinning. The 18-JVM run had producer CPU usage of at least 1.52 CPU-equivalents averaged over captured samples, service approximately 0.25, and whole shared cgroup approximately 3.06. These measurements alone cannot identify the bottleneck.

The two 8-JVM trials are sequential, one run each, not an adequately randomized/replicated A/B performance study. Background services and workload-state growth may influence them. The p95 reduction in the partitioned run **does not establish** a causal improvement or general performance gain. The high-lag 18-JVM trial diagnoses a failed load-generation target under a shared-host budget, **not** the service's throughput capacity.

## Reproduce safely

In one terminal on the service host (where a valid test database and RabbitMQ already run):

```sh
DEROYQUE_SERVICE_CPUS=2,3,4 DEROYQUE_PROVIDER_CPUS=2,3,4 \
  DEROYQUE_API_PORT=18131 DEROYQUE_PROVIDER_PORT=18132 \
  python3 -u scripts/dev_demo.py
```

Set `DEROYQUE_SIM_WRITE_TOKEN` and optionally `DEROYQUE_SIM_READ_TOKEN` from the private credential file, never from the ZIP. Run the generator on the same machine for CPU-affinity testing:

```sh
./simulator/build.sh
python3 simulator/saturation.py --tier 8:70:350 \
  --url http://127.0.0.1:18131 --service-pid "$SERVICE_PID" \
  --expected-service-cpus 2,3,4 --producer-cpus 0,1 \
  --threads service-and-producer --jfr --heap-max-mb 32 \
  --output-dir simulator/results/new-m6-experiment
```

The output directory must not exist beforehand. The service PID must be a running `demo.DeroyqueApplication` instance that was launched with the expected affinity.

**For truly independent load-generator hosts:** install the standalone simulator on a separate Linux server, set an externally reachable authorized service URL, and pass `--allow-remote`. Do not assume same-host clock alignment across multiple generator machines or merge per-JVM in-flight timing across hosts naively. No distinct host was available or tested in this milestone. For such a deployment, a production-grade orchestration layer, network/authentication isolation and clock-skew-aware aggregation remain future work. **CPU placement is not physical separation.**

## Test and artifact limitations

- 36/36 Python unit tests passed after the final changes (0/36 failures).
- Direct Java/Python modules and the service implementation were retained; this milestone did not implement Spring Boot or a physically separate generator host.
- No verified remote-provider end-to-end completion throughput, production readiness, or exactly-once external effect is claimed.
- The JFR events and thread dumps are evidence but may affect the benchmark themselves.
- Results contain no private demo tokens; generated temporary PostgreSQL schemas and SQLite files are not distributed.
