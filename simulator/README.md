# Deroyque Input Simulator — first-class workload generator (Milestone 04)

This is a **standalone ingress-client product** shipped beside the Deroyque Service and the independent Mock Provider. It sends actual HTTP `POST /api/v1/requests` requests to the application's public API. It does not modify the service's database or impersonate the external provider. Python 3 orchestrates **independent Java 21 JVMs**, each using virtual threads, a bounded HTTP concurrency pool, and an independent HTTP client. The coordinator audits all responses and optionally retrieves their persisted states.

## Build, dependencies and safety

```sh
./simulator/build.sh
python3 -m unittest discover -s simulator/tests -v
```

Requires Java 21/JDK (`javac`, `jar`), Python 3.10+ and **PyYAML** (`pip install PyYAML`) **only for YAML configurations**. CLI-only execution and JSON report comparisons use Python's standard library. No Maven, broker, database, service or provider libraries are required by the simulator itself.

The target defaults to `http://127.0.0.1:18081`, and **non-loopback addresses require explicit `--allow-remote`**. Run load tests only against systems you own or are authorized to stress. Credentials are environment variables (`DEROYQUE_SIM_WRITE_TOKEN`; optionally `DEROYQUE_SIM_READ_TOKEN` for `--verify-state`). Never put credentials in YAML, CLI arguments or run IDs. The output directory must not already exist, preserving previous evidence. Default resource safety restricts total estimated producer memory use and at most 256 JVMs; `--unsafe-resource-override` is an explicit escape hatch for an appropriately isolated lab.

## Run a workload

Start your service and provider first. On this Linux laboratory, `python3 -u scripts/dev_demo.py` starts an isolated ephemeral-schema demo against the *already running* PostgreSQL and RabbitMQ. In another shell, set the development tokens printed into the private credential file (do not publish it).

```sh
export DEROYQUE_SIM_WRITE_TOKEN='demo-token-from-private-file'
export DEROYQUE_SIM_READ_TOKEN='demo-token-from-private-file'
python3 simulator/run.py --config simulator/examples/steady.yaml \
  --url http://127.0.0.1:18081 \
  --output-dir simulator/results/new-steady

# Override YAML explicitly on CLI; choose safe JVM count from cgroup headroom
python3 simulator/run.py --config simulator/examples/burst.yaml \
  --auto-jvms --auto-max-jvms 64 --heap-max-mb 32 \
  --requests 600 --keys 150 --output-dir simulator/results/new-burst

python3 simulator/compare.py \
  simulator/results/new-steady/summary.json \
  simulator/results/new-burst/summary.json \
  --output-prefix simulator/results/my-comparison
```

`compare.py` generates a Markdown table and CSV. It does not assume different workloads, profiles or runs are causally comparable.

## Profile semantics

| Profile | Schedule |
| --- | --- |
| `steady` | Uniform global arrival pacing. `--rate` is target attempts/s, `0` means maximum possible burst. Optionally `--duration-seconds` derives `requests/duration` when rate is 0. |
| `ramp-up` | Linearly rising **planned** arrival rate from `--ramp-start-rate` to `--rate` over `--duration-seconds`. If the requested count exceeds that window's integrated arrivals, it continues at the final rate. |
| `burst` | Repeated high-rate periods (`--burst-rate` during `--burst-width-seconds` of every `--burst-period-seconds`), with `--rate` as baseline outside the bursts. This profile does not use `--duration-seconds`: count and rates determine duration. |
| `soak` | Schedules `--requests` evenly across the entire `--duration-seconds`, including the last planned send. An optional nonzero `--rate` must agree with requests/duration to within 10%. |

Scheduling is *open-loop at the plan level*: all JVMs receive a deterministic partition of the same global timeline. Actual dispatch is bounded by each agent's `--concurrency` semaphore, OS scheduling and downstream backpressure. Recorded **schedule lag p50/p95/p99** quantifies misses of the planned timeline. A planned 500 req/s is never reported as 500 observed req/s without evidence. `--start-delay-ms` provides a common wall-clock start barrier **only for JVMs on this same host**.

## Collision distributions

The first `--keys` sequence numbers introduce every key once using the original payload, making the generated experiment self-contained and auditable. After the first wave:

- `sequential`: cycle through all key slots (`sequence % keys`)
- `unique`: every sequence has a unique key; requires `keys == requests`
- `hotspot`: with probability `--hotspot-percent`, select from the first `--hotspot-keys-percent` percent of keys, otherwise select uniformly over the full set
- `skewed`: sample `int(keys * U ** --skew-power)` using a uniform random U. **This is a controllable power-skew, not a mathematically exact Zipf distribution.**

`--conflict-percent` controls the fraction of reuse attempts that mutate the immutable business payload; the rest are idempotent replays. `--seed` makes slot selection, variant choice and schedule deterministic for the same configuration. Generated run IDs are unique by default to avoid cross-run collisions.

## Resources and controls

- `--jvms N` and `--concurrency N`: separate Java producer processes and maximum outstanding HTTP calls per process.
- `--auto-jvms --auto-max-jvms N`: choose an upper bound from cgroup v2 *free memory at launch*, number of POSTs, the configured maximum and 256. Safety estimation uses `heap-max + 48 MiB native allowance` per JVM, and reserves additional headroom. The heuristic is not proof against OOM, particularly under broker/database load.
- `--heap-min-mb`, `--heap-max-mb`, `--active-processors`: per-JVM `-Xms`, `-Xmx`, `-XX:ActiveProcessorCount`. JVMs share the host's actual CPU allowance; the flag does not allocate extra cores.
- `--timeout-ms`, `--start-delay-ms`, `--max-error-percent`, `--verify-state`, `--await-done-seconds`, `--run-id`, `--url`, `--allow-remote`, `--output-dir` control transport, safety and reporting.
- YAML values are strictly typed, unknown keys rejected, and **explicit CLI arguments override YAML**. Secrets are intentionally not configurable through YAML.

`summary.json` records the exact chosen number of JVMs, peak distinct JVMs with HTTP in flight, peak requests in flight, successful admissions, conflicts, errors, client-side POST latency percentiles, schedule lag, POST throughput and final checked statuses. Each JVM has its own `plan-*.tsv`, `agent-*.tsv` and log for diagnosis. `POST/s` means API **admission attempts**, not completed provider effects or billing transactions. Zero recorded invariants is not a proof of distributed exactly-once operation.

## Milestone 04 verification

The simulator test suite passed **0/17 failed (17/17 passed)** after the last code change. Five real profiles were executed against Java 21 + PostgreSQL 17.11 + RabbitMQ 4.4.0 and a separate SQLite-backed provider, with **0/1,840 unexpected HTTP/transport failures (1,840/1,840 admitted or expected conflicts)** and no auditor invariant violations. Detailed run data and a comparative report are under `results/m4-*`. The resource-bounded auto-stress run selected 13 JVMs; none of the test runs claims to have reached the maximum throughput capacity of the service. Tests executed on one host, not a geographically distributed system.

### Remaining improvements

Workload scenarios are deterministic but the producer still uses the host wall-clock barrier; distributed-host launch coordination, closed-loop RPS feedback, CPU/heap/GC telemetry per JVM, real saturation point search, long-running soak/endurance campaigns, restart/kill of individual producers, and verified end-to-end provider **completion** rates remain future work. The current short soak example is a *profile acceptance check*, not an endurance certification.

## Milestone 05 — Saturation Explorer + thread analysis

`python3 simulator/saturation.py` now runs staged load and collects per-process/cgroup telemetry and bounded thread diagnostics; `--jfr` captures JFR pinning, monitor, thread-park and GC events where configured. The service JVM PID is explicit, and producer JVMs are discovered as direct children of the coordinator process. `python3 simulator/saturation_compare.py report1/saturation.json report2/saturation.json --output-prefix output/multi-run` compares tiers while retaining `n/a` for missing data.

The executed tests, observed limits, JFR pinning and caveats are documented in [`docs/MILESTONE-05.md`](../docs/MILESTONE-05.md). Measured throughput remains **POST ingress**, never externally completed effects. CPU and JFR evidence help narrow investigations but do not independently establish causality or server-only saturation.

## Milestone 06 — CPU placement and focused JFR

For controlled same-host affinity experiments, add `--producer-cpus 0,1` to `run.py` (also a YAML string option) or `saturation.py`. The actual Java processes are launched using `taskset`; the observer verifies their masks. Use `--expected-service-cpus 2,3,4` with a service **already launched** under that affinity, which verifies OS thread masks and disjoint producer/service placement. `scripts/dev_demo.py` supports `DEROYQUE_SERVICE_CPUS` and `DEROYQUE_PROVIDER_CPUS` at startup. CPU affinity **does not** reserve or isolate CPU capacity, nor move RabbitMQ/PostgreSQL to separate hosts. The service and load generator still share cgroup CPU and memory quota.

The JFR recorder now generates a focused configuration, enabling low-threshold pinning and monitor events with stacks, and reports duration buckets plus longest captured stacks. Events themselves do not prove a specific culprit. The real three-trial comparison and methodology are in [`docs/MILESTONE-06.md`](../docs/MILESTONE-06.md). A standalone simulator may target a separately managed, authorized service host using `--allow-remote`, but remote orchestration was not verified in this milestone.

## Milestone 07 — distributed input simulation

The ingress simulator can now share a **single deterministic global workload** across separate producer hosts. `distributed_prepare.py` prepares host-specific scripts; each host runs `run.py` with its shard identity and a common UTC start barrier; `distributed_merge.py` audits all records **globally**. A shard's local success is explicitly `SHARD_PASS_GLOBAL_AUDIT_PENDING`, not a global success claim. The shared key space creates cross-host replay and conflict races.

**Important:** The original JVM process count and throughput measurements from Milestone 07 were performed with **two coordinator processes on one Linux VM**, not two physical Linux hosts. To run the prepared scripts on real independent machines, use the secure tunnel/TLS and clock-synchronization instructions in [`../docs/MILESTONE-07.md`](../docs/MILESTONE-07.md). For benchmarks, cross-host peak concurrency and overall QPS are deliberately omitted until clock offsets/service-side metrics are verified.

## Optional service-side instrumentation (Milestone 08)

This is an **explicit evaluator opt-in**, disabled by default in Deroyque. Start the lab service with `APP_LAB_METRICS_ENABLED=1` only for comparative measurements; then capture tagged admission counters using `python3 simulator/server_metrics.py` (in the standalone package, `python3 server_metrics.py`) after all host shards complete. Pass the saved JSON to `distributed_merge.py --server-metrics`. When instrumentation is off, omit `--server-metrics`; global service-side throughput remains unknown rather than invented. Instrumentation changes the cost of request handling, so report enabled/disabled settings with each benchmark. See the integrated project's `docs/MILESTONE-08.md`.

## Milestones 39–43 — Single-clock gateway witness (optional laboratory tool)

Start independently managed application listeners before starting the witness.
For a **loopback-only** example with two service JVM listeners on 18081/18082:

```sh
python3 -u simulator/ingress_witness.py --run-id lab-run-001 \
  --backend http://127.0.0.1:18081 --backend http://127.0.0.1:18082 --port 18088
# Point both producer shards at http://127.0.0.1:18088 with the SAME run ID.
# Once all shards finish, capture the final snapshot; do not copy credentials:
python3 -c 'import urllib.request; print(urllib.request.urlopen("http://127.0.0.1:18088/__witness/report").read().decode())' > witness.json
python3 simulator/distributed_merge.py --gateway-witness witness.json \
  simulator/results/lab-run-001-host-000 simulator/results/lab-run-001-host-001
```

**Never expose the witness directly on a network.** It binds exclusively to loopback and permits only loopback backend listeners. For distributed hosts, use authenticated encrypted tunnels and document their conditions; distinct kernel reports are still not verified physical independence. The witness forwards bearer authorization without logging it. Gateway QPS includes gateway overhead and is NOT provider effect throughput; errors, inconsistent counters and incomplete evidence block success. The real Spring Boot/SQLite acceptance is a separate GitHub CI scenario; see `docs/MILESTONE-42.md`.
