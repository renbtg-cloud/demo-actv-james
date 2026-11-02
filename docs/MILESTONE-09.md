# Milestone 09 — measured cost of optional observability

## Scope and status

Existing codebase remains one integrated Java 21 service + independent input simulator + SQLite-backed HTTP Mock Provider. There is no new POC branch. This milestone adds a **reusable A/B runner** under `scripts/benchmark-metrics-ab.py`, with isolated ephemeral application/schema/queue/provider *per trial*, and observational CPU/RSS/cgroup measurement. The normal service behavior and opt-in flag did not change.

**Default remains OFF.** When `APP_LAB_METRICS_ENABLED` is absent or `0`, no `IngressMeasurements` instance exists and the authenticated laboratory metrics route responds HTTP 404. To opt in, set `APP_LAB_METRICS_ENABLED=1` before service startup. All metrics remain confined to the current service JVM and are accessible only with the read token. This is not a production endpoint.

## Experimental protocol

- Run four service instances sequentially in **OFF → ON → ON → OFF** order, each with its own disposable PostgreSQL schema, RabbitMQ queue and SQLite database. Do not stop or reset shared PostgreSQL/RabbitMQ infrastructure.
- Each trial uses a new run ID but identical plan parameters (same deterministic seed, key slots and conflict distribution). Six or eight independent Java producer JVMs are involved, not simulated worker threads.
- Capture client request codes, throughput, p95 HTTP latency, schedule lag, and JVM-service/cgroup CPU time sampled via `/proc` and cgroup v2. CPU measurements include startup scheduling delays; this is a limitation.
- After an ON trial, capture the service-side monotonic timestamp snapshot and verify its POST count and each HTTP status against the client. An OFF trial must continue returning HTTP 404. No JFR is recorded during A/B experiments, to avoid additional profiler overhead.
- Validate the controller’s own cleanup behavior: controller signals only its own disposable application/providing child processes; other Java services, shared PostgreSQL and RabbitMQ remain untouched. An early test run exposed orphaned children when the demo controller exited; this was fixed and the full sequence rerun.
- Credentials, demo logs containing example bearer tokens and runtime SQLite files remain in `.local` and **must not** be included in redistributable archives.

## Live results on 2026-11-02 (one Linux, four shared CPUs)

**No unexpected responses:** 0/960 failures; 960/960 HTTP responses were expected in the first four trials; 0/1,600 failures; 1,600/1,600 were expected in the second four trials. Combined: **0/2,560 failed; 2,560/2,560 passed**. Each trial separately passed the simulator's idempotency and reply audits.

### A/B set 1 — requested 65 POST/s; six JVMs, 240 POSTs per trial

| Trial | Instrumentation | Actual client POST/s | Client p95 HTTP (ms) | p95 schedule lag (ms) |
|---:|---|---:|---:|---:|
| 1 | OFF | 64.412 | 274.2 | 30.11 |
| 2 | ON | 64.882 | 346.35 | 29.84 |
| 3 | ON | 64.481 | 501.78 | 65.86 |
| 4 | OFF | 64.291 | 344.46 | 30.91 |

Mean client POST/s: OFF **64.35**, ON **64.68**. Mean p95: OFF **309.33 ms**, ON **424.06 ms**. The p95 difference is **not** a proven instrumentation effect: there are only two observations per condition, and independent resource contention exists.

### A/B set 2 — requested 150 POST/s; eight JVMs, 400 POSTs per trial

| Trial | Instrumentation | Actual client POST/s | Client p95 HTTP (ms) | p95 schedule lag (ms) |
|---:|---|---:|---:|---:|
| 1 | OFF | 116.618 | 823.73 | 584.84 |
| 2 | ON | 119.367 | 800.24 | 479.78 |
| 3 | ON | 120.627 | 839.45 | 533.37 |
| 4 | OFF | 119.796 | 896.45 | 617.32 |

Mean actual client POST/s: OFF **118.21**, ON **120.00**. Mean p95: OFF **860.09 ms**, ON **819.84 ms**. The sign of the p95 difference *reverses* relative to set 1. The generator itself missed the 150 POST/s schedule, and the schedule lag was hundreds of milliseconds. **Neither a throughput improvement nor a performance regression can be causally attributed to the feature.**

### What the numbers do and do not show

The same host also runs PostgreSQL, RabbitMQ and the older laboratory service. CPU quota and memory are shared, and every trial launches a fresh Java service. Hence JIT warmup, database workload, scheduling, backlog, and noise differ. The experiment demonstrates an operationally safe opt-in mode and establishes a repeatable measurement protocol, **not** a statistically sound estimate of its intrinsic overhead, a maximum supported request rate, or production capacity. Client-admission QPS is not provider-completed effects per second.

The service-side ON snapshots have a single-instance monotonic clock. OFF cannot expose an equivalent server-side duration without introducing instrumentation. Therefore all OFF/ON comparisons above deliberately use client-side timing, with the server data used for count verification only.

## Test results and failures discovered while building

- New A/B harness unit tests: **0/8 failed; 8/8 passed**.
- Existing simulator tests: **0/52 failed; 52/52 passed**.
- Java HTTP adapter checks: **0/37 failed; 37/37 passed**.
- One initial ABBA attempt was **incomplete**, not counted: the test controller left disposable child processes orphaned after a shutdown, which caused the next run to encounter an incorrect service instance. Controller supervision and credential-to-instance readiness checks were corrected. Both subsequent *complete* four-trial sequences succeeded.

## Reproduce (authorized local laboratory only)

Prerequisites: shared PostgreSQL 17.11 and RabbitMQ 4.4.0 already available at the documented loopback ports; Java 21, `psql`, Python 3 with `cryptography`; input simulator JAR built. The script compiles the service on each disposable launch. Fresh output directories are mandatory; evidence cannot be overwritten.

```sh
./scripts/build.sh
./simulator/build.sh
python3 scripts/benchmark-metrics-ab.py --output-dir ./simulator/results/my-ab-run \
  --jvms 6 --concurrency 8 --requests 240 --keys 80 --rate 65
```

The optional `--private-dir` must be an unpublished directory. Generated credentials never belong in CI logs, copied reports or ZIP archives. See `COMPARISON.md` and `COMPARISON.json` for both runs; every trial has a `load/summary.json`, `result.json`, and resource telemetry.

## Further work

For any meaningful overhead estimate, separate the load-generator host, control warmup, repeat more trials with randomized or counterbalanced order, record server-only CPU and capacity measurements with calibrated external observation, and compute confidence intervals. Instrumentation itself remains opt-in; do not turn it on as a side effect of requesting diagnostics or sending load headers.
