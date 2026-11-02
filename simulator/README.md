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

## Multi-host launch integrity and bounded clock preparation (Milestones 72–77)

The following steps are for an **authorized** lab with separately managed hosts;
they do not create remote machines or make remote access secure for you.

1. Prepare a single launch plan on the trusted collector, e.g.:
   `python3 simulator/distributed_prepare.py --url https://authorized.example --shards 2 --jvms-per-host 4 --requests 160 --keys 40 --run-id multi-demo --output-dir /private/launch-multi-demo`.
   Keep `manifest.json` and the launch scripts in a collector-controlled directory.
   Transfer the appropriate launcher to each authorized host over an authenticated
   channel, and provision `DEROYQUE_SIM_WRITE_TOKEN` securely on that host.
2. Coordinate UTC across hosts with trusted time synchronization. For additional
   *bounded* offset evidence, start `python3 simulator/clock_probe.py serve --port 18193`
   on the collector and create a separate authenticated SSH tunnel to its loopback
   port on every producer. On each host run `python3 simulator/clock_probe.py probe
   --url http://127.0.0.1:18193/clock --run-id multi-demo --reference-id collector-A
   --shard-index N --output clock-N.json` with N equal to its shard index. The
   collector runs `clock_probe.py collect --run-id multi-demo --reference-id collector-A
   --output clock-all.json clock-0.json clock-1.json`. The probe's HTTP URL is local
   **only because the remote hop has already been secured outside this program**.
3. Run the prepared per-host scripts; transfer their complete result folders to a
   trusted collector. Before modifying any results, run
   `python3 simulator/distributed_integrity.py --seal --index /private/multi-demo-index.json
   simulator/results/multi-demo-host-000 simulator/results/multi-demo-host-001`.
   Secure the digest index separately. The index proves integrity relative to what
   was collected and sealed, not that producers were honest.
4. Verify launcher digests with `distributed_integrity.verify_launch_scripts()`
   in the collector and reconcile with:

```sh
python3 simulator/distributed_merge.py \
  --launch-manifest /private/launch-multi-demo/manifest.json \
  --evidence-index /private/multi-demo-index.json \
  --clock-evidence /private/clock-all.json \
  --require-distinct-kernels --strict-external-preflight \
  --verify-state --output /private/multi-demo-merged.json \
  simulator/results/multi-demo-host-000 simulator/results/multi-demo-host-001
```

No component here establishes authenticated machine identity, verifies SSH tunnel
configuration, or guarantees clocks remain stable for an entire run. The merge
will not invent cross-host peak concurrency, physical independence or provider
completion throughput from these fields. Execute the full physically separated
campaign only after host provenance and monitoring are independently verified.

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

## Milestones 44–46 — Measurement limits and trial comparison

The witness histogram captures **backend HTTP round-trip latency as seen at the
loopback gateway**, not queue completion, provider effects or downstream client
receipt. Failed socket writes to the client are counted and cause the global
auditor to refuse an unqualified PASS. The gateway must not be exposed directly
to external networks.

After three or more complete, independent runs with `distributed_merge.py
--verify-state --gateway-witness ... --output merged.json`, use
`python3 simulator/trial_compare.py run1/merged.json run2/merged.json
run3/merged.json --output benchmark-comparison.json`.
The comparator demands identical workload SHA-256, terminal states and gateway
evidence and produces descriptive median/MAD rather than an invented capacity
limit. See the detailed methodological limits in `docs/MILESTONE-46.md`.

## Milestones 49–50 — Genuine sustained and repeated real-Spring CI

`gateway-soak` runs 240 POSTs over ~120 seconds with 60 unique idempotency
keys through two Spring Boot processes, PostgreSQL, RabbitMQ, the independent
SQLite provider and the one-clock gateway. `gateway-trials` repeats the whole
freshly isolated Spring experiment three times, then strictly compares their
reconciled witness reports and terminal state evidence. Both are distinct
workflow jobs with dedicated fail-closed assertion gates. **Two minutes is not
endurance certification**, and repetition on a single hosted CI runner does not
establish physical independence or benchmark causality.

## Signed multi-host evidence (Milestones 81–83)

Each producer host holds an **operator-managed Ed25519 private key** outside
this repository. The collector must independently enroll the matching public
key and host identity before the experiment; do not trust a key supplied in the
same untrusted evidence bundle being verified.

On a producer host, after `simulator/run.py` has finished and all `agent-*.tsv`
files and `summary.json` are in place:

```sh
umask 077
openssl genpkey -algorithm Ed25519 -out /private/host-signing-key.pem
openssl pkey -in /private/host-signing-key.pem -pubout -out /private/host-public.pem
python3 simulator/host_signed_evidence.py sign \
  --folder simulator/results/RUN-host-000 --run-id RUN \
  --host-id host-zero --shard 0 \
  --private-key /private/host-signing-key.pem \
  --output /private/RUN-host-000-signed.json
```

Use **distinct** private keys for every host and transfer public keys to the
collector over a separately authenticated channel. Collect an offline JSON
mapping of host IDs to PEM-encoded public-key strings (the actual PEM contents,
with escaped newlines), never private keys. Keep this pin inventory outside
untrusted result directories. The same key must not be enrolled twice.

Then invoke `simulator/distributed_merge.py` with
`--host-pins collector-pins.json`, one `--signed-host-report` per shard,
and `--require-host-signatures`. Include `--strict-external-preflight`,
`--require-distinct-kernels`, `--launch-manifest`, `--evidence-index`, and
`--clock-evidence` for the stronger combined evidence gate. Report order may
vary; each signed shard index determines its correct evidence directory.

The signatures prove possession of keys pinned before collection; they do NOT
prove that keys are on physically separate machines or that the operator's
host inventory is authentic. Run the independent-host experiment only after
the operator validates host ownership and network/security boundaries.


## Collector-bound signing sessions, signed clock samples and verdict receipts (M84–M92)

The collector should **retain independently** its launcher `manifest.json`,
operator-approved `pins.json` (host ID to PEM public key), a `session.json`
created by `host_session.py`, and the signing-key custody policy. Enrollment
must not be based on keys received in the same untrusted result upload.

After generating the launch manifest and enrolling one distinct host public key
per shard, on the **collector** (no private key needed):

```sh
python3 simulator/host_session.py --manifest /trusted/manifest.json \
  --pins /trusted/pins.json --hosts host-zero,host-one \
  --output /trusted/session.json
```

Distribute the session, launcher, manifest and relevant pin inventory over an
**authenticated transport** before running. Each host signs its own completed
shard with an owner-only local private key; `--clock-report` is optional and
requires the corresponding independently collected per-host clock report:

```sh
python3 simulator/host_signed_evidence.py sign \
  --folder simulator/results/RUN-host-000 --run-id RUN \
  --host-id host-zero --shard 0 --private-key /private/host-signing-key.pem \
  --session /trusted/session.json --manifest /trusted/manifest.json \
  --pins /trusted/pins.json --clock-report /private/host-clock.json \
  --output /private/host-000-proof.json
```

On the collector run `distributed_merge.py` with all shard folders, the
`--host-session`, `--host-pins`, `--launch-manifest`, and one
`--signed-host-report` per shard, plus `--require-host-session`. If individual
clock reports were signed, supply one `--signed-clock-report` per shard **and**
the matching `--clock-evidence` aggregate. Missing or contradictory material
causes FAIL, not a weaker implicit fallback. Store the final PASS JSON outside
the untrusted shard directories.

An optional **collector-local** one-time publication journal may then accept
the same signed run only once:

```sh
python3 simulator/collector_ledger.py --ledger /trusted/accepted-runs.sqlite \
  --session /trusted/session.json --verified-merge /trusted/final-pass.json
```

An authorized independent collector signing key can produce a detached result
receipt, which an evaluator can verify using a separately enrolled collector
public key:

```sh
python3 simulator/collector_receipt.py sign --result /trusted/final-pass.json \
  --session /trusted/session.json --receipt /trusted/receipt.json \
  --collector-key /private/collector-private.pem
python3 simulator/collector_receipt.py verify --result /trusted/final-pass.json \
  --session /trusted/session.json --receipt /trusted/receipt.json \
  --collector-key /trusted/collector-public.pem
```

**Limits:** All files must be curated by the collector over authenticated
channels. Challenge and signatures reject key/role/report substitution in this
scope but cannot prevent a compromised key, a dishonest collector or two
colluding actors from fabricating self-reported kernel identities. The ledger
is local to one securely managed collector filesystem; the receipt authenticates
its claim, not physical host provenance. Never bundle private signing keys or
bearer tokens inside Git commits, source ZIPs or shared evidence packages.

## Portable signed shard transport (Milestones 93–98)

After a remote host has completed its local simulator, independently obtained
its collector session, and signed the shard with its pinned Ed25519 key:

```sh
python3 simulator/shard_bundle.py create \
  --folder simulator/results/RUN-host-000 \
  --signed-envelope /secure/host-000-envelope.json \
  --output /secure/host-000.zip

# Transfer each host-00N.zip via your own authenticated channel to the collector.
# Run on the trusted collector; files manifest, keys and session are NEVER taken
# from the incoming ZIPs.
python3 simulator/shard_collection.py \
  --manifest /trusted/manifest.json \
  --session /trusted/session.json \
  --pins /trusted/host-public-keys.json \
  --output-dir /trusted/received/RUN \
  /incoming/host-000.zip /incoming/host-001.zip
```

The collector *does not* start the machines, authenticate an SSH connection or
prove that self-reported hosts are physically independent. Successful transport
collection is not a global application PASS; run the separate global merger and
server/provider state auditors on the verified imported shard directories. The
ZIP deliberately holds no private keys. Linux `renameat2` is required for
atomic no-replace publication. Keep incoming and trusted directories
separately controlled. The integrity verifier rejects compressed ZIP entries.

For runs that are intended to use multiple kernels, pass
`--require-distinct-kernels` to `shard_collection.py`. This rejects missing or
matching run-salted kernel boot IDs, but differing self-reports are not
cryptographic hardware attestation. Keep the physical host inventory and
independent clock reference as separate experimental records.
