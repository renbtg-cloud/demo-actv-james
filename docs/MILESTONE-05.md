# Milestone 05 — Saturation Explorer and JVM Diagnostics

This milestone extends the **same first-class input simulator**; it does not replace the Java service or the independent external Mock Provider.

## Executable additions

- `simulator/saturation.py`: incremental load tiers using **real, independent Java 21 producer processes**, sending HTTP POSTs through the existing workload generator. Each tier records its own `summary.json`, `telemetry.csv`, JVM diagnostics, optional `service.jfr`, and overall `saturation.json` / Markdown / CSV reports.
- `simulator/thread_diagnostics.py`: bounded `jcmd Thread.dump_to_file -format=json`, `Thread.print -l`, and optional bounded JFR `profile` capture for an explicitly identified Deroyque JVM. It samples cgroup v2 CPU/memory/throttling/OOM, service CPU/RSS/OS threads, and separate producer-process counters.
- `simulator/saturation_compare.py`: combines tier summaries across independent experiments, explicitly marking unavailable or incorrectly captured historical producer CPU metrics as `n/a` and resource-guard rejections as **not executed**.
- `simulator/tests/test_saturation.py`: parser, guard, percentile/verdict, CPU aggregation, dump/JFR interpretation and report checks.

### Run (authorized laboratory only)

Build the existing simulator and start the service and external provider separately. Load credentials into **environment variables**, never commit them or add them to CLI/YAML. Supply the **actual service JVM PID**:

```sh
./simulator/build.sh
python3 -m unittest discover -s simulator/tests -v
python3 simulator/saturation.py \
  --url http://127.0.0.1:18481 \
  --service-pid YOUR_SERVICE_JVM_PID \
  --tier 4:35:210 --tier 8:80:400 --tier 12:140:560 \
  --output-dir simulator/results/new-benchmark \
  --heap-max-mb 32 --heap-min-mb 16 \
  --concurrency 8 --threads service-and-producer --jfr --verify-state
```

Each tier argument is `JVMS:PLANNED_POSTS_PER_SECOND:TOTAL_POSTS`. Output directory must not exist. The target defaults to loopback and requires `--allow-remote` for non-loopback (use only for systems you are authorized to stress). The resource guard estimates native+heap memory per JVM, reserves headroom for other processes, and refuses a tier when the budget is insufficient. It does **not** silently override safety constraints.

### Validation actually completed

- **0/29 simulator unit/integration tests failed; 29/29 passed** after the comparison and diagnostic changes.
- **0/3,090 HTTP POSTs produced unexpected results** across six completed real tiers; **3,090/3,090 received expected status codes**, with no idempotency violations reported by the ingress auditor. All were executed against a real Java 21 service, PostgreSQL 17.11, RabbitMQ 4.4.0 and independent SQLite-backed HTTP provider.
- Real concurrent producer-JVM counts observed: **4, 8, 10 and 12**. Maximum actually tested in this round: **12 JVMs**. Attempts at **13 and 15 JVMs** were refused by the memory guard **before any HTTP requests were sent**; they are not failures of the target service, nor successful high-concurrency tests.
- Resource environment: **4 CPUs, 4 GiB cgroup memory limit**, shared between producer JVMs, service, provider, database, broker and other resident processes. No cgroup OOM kills were observed during these runs.

| Planned JVMs | Target POST/s | Observed POST/s | POSTs | p95 client ms | Schedule lag p95 ms | Interpretation |
|---:|---:|---:|---:|---:|---:|---|
| 4 | 35 | 35.1 | 210 | 75 | 5 | sustained |
| 8 | 80 | 79.9 | 400 | 404 | 38 | sustained |
| 12 | 140 | 136.4 | 560 | 755 | 470 | sustained within configured tolerance |
| 12 | 240 | 179.2 | 720 | 897 | 809 | generator/shared-host scheduling lag; server bottleneck **not established** |
| 10 | 110 | 109.1 | 440 | 740 | 186 | sustained |
| 12 | 190 | 165.4 | 760 | 823 | 627 | sustained under threshold of 80% target and 700ms lag; target not fully achieved |

The threshold is **heuristic**, and varies by benchmark invocation. For the 240 POST/s run the configured lag ceiling was 500ms and the minimum ratio was 80%; the run did not sustain the target. POST/s is **API admission throughput**, not provider effects or terminal completions.

### Threads, JFR and resource findings

- Thread dumps and JFR recordings were collected on the actual service JVM and bounded producer samples. Platform-thread states and lock ownership indicators are separate from virtual-thread stack containers. JDK 21 JSON dumps do not give a trustworthy `RUNNABLE/BLOCKED` state for every virtual thread.
- JFR `jdk.VirtualThreadPinned` events were observed in multiple tiers, including **5** events in the 12-JVM/140 POST/s run, **4** in the 12-JVM/240 POST/s run, and **7** in the 12-JVM/190 POST/s run. The last run had 209.3ms aggregated event duration. The emitted stack samples were shallow JDK frames, insufficient to identify an owning application lock or claim a JDBC/RabbitMQ root cause.
- During the 12-JVM/190 POST/s tier, the service averaged about **0.33 CPU cores**, the sampled producer JVMs at least **1.61 cores**, while all processes in the shared cgroup consumed approximately **3.87 cores**. CPU throttling events increased. The service alone was not shown to saturate its allocated cores; generator and shared infrastructure contention are possible explanations.
- An early CPU aggregation implementation incorrectly dropped producer CPU usage when the short-lived JVMs exited. This was detected and corrected. Historical producer CPU columns are explicitly marked `n/a` in the comparison rather than retaining misleading zeroes. The new counter is still a **sampling lower bound**, not precise lifetime CPU accounting.
- Earlier 15-JVM rejection was originally classified generically as a workload failure; inspection of the run log established it was a memory-budget preflight rejection. The comparator annotates this without modifying original evidence.

### Boundaries and gaps

This is **not** a demonstrated maximum throughput of the Deroyque server. All processes share one Linux cgroup and compete for CPU/memory, so the measured limit may come from the producer, database, broker, service, or shared scheduling. JFR itself and thread dumps add measurement overhead. There is no automated thread-to-PostgreSQL-connection attribution, no retained causal call graph across retries, no independent-host load generators, and no extended soak test. A small number of JFR pinning events does not prove that pinning caused lost throughput; zero would not prove absence either.

Preserved run data and raw JFR samples are available in `simulator/results/m5-*`; see `simulator/results/m5-comparison.md`. The manifest verifies packaged file contents. Private demo tokens, temporary SQLite databases, .local files and shared infrastructure binaries are intentionally excluded.
