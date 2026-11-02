# Milestone 04 independent audit notes — input simulator

- **Source:** unchanged service and provider core from Milestone 03; modified `simulator/run.py` and `simulator/src/main/java/demo/simulator/LoadAgent.java`; new `simulator/workload.py`, `compare.py`, `examples/*.yaml` and additional tests.
- **Build:** `./simulator/build.sh` compiled Java 21 successfully. The Python coordinator compiles with Python 3.13.5.
- **Tests:** 0/17 failed, 17/17 passed (7 legacy + 10 new) after final change. One intermediate parser merge failure, another test harness error (incorrect generator max), a test expectation typo, and an intermittent fake-server backlog exhaustion (1/120 transport failures when testing the extracted ZIP) were repaired and rerun; they are not counted as final successes without rerun.
- **Real integration:** five complete runs, 0/1,840 unexpected HTTP/transport statuses, zero audit violations, with 8, 12 or 13 JVMs; actual per-run values in `simulator/results/m4-comparison.md` and original `summary.json` files.
- **Limits:** 4 GiB cgroup memory and 4 CPUs equivalent at test time. Auto-JVM guard selected 13 JVMs for a 32-MiB Java heap, reserving native-memory and broker/database headroom. All independent JVMs are on one Linux host.
- **Open-loop timing:** `schedule_lag_ms` measures late client launches. Particularly on `burst`, the actual achieved throughput lagged the target, so no claimed ability to sustain the peak requested rate.
- **External effects:** no exactly-once guarantee claimed. End-to-end external provider completions/throughput were not benchmarked in this milestone; the audit checks API admission and optional immutable-state reads.
- **Packaging:** excluded all `.local` development credentials, SQLite database state, hidden files containing tokens, Python cache, transient build classes, root obsolete SHA manifest, and old logs unrelated to Milestone 04.
- **Not tested here:** cross-host clocks, sustained hours-long soak, failover during this workload batch, automatic CPU saturation search, other distributed broker state stores and Spring Boot adapter.
