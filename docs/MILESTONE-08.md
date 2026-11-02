# Milestone 08 — opt-in ingress observability and shard reconciliation

**This is laboratory-only instrumentation, DISABLED by default.** The application and `scripts/dev_demo.py` both use `APP_LAB_METRICS_ENABLED=0` if the evaluator does not explicitly enable it. Normal request handling, idempotency and recovery are unaffected. No per-request observation structures are allocated or updated when disabled (`RestApi.labMetrics == null`); the metrics endpoint responds with HTTP 404 even to valid read-token holders. A tagged request still behaves normally.

## Explicit evaluator opt-in

```sh
# For the local, disposable demo only. Requires existing PostgreSQL/RabbitMQ.
APP_LAB_METRICS_ENABLED=1 python3 -u scripts/dev_demo.py
```

For a separately managed Java service, set `APP_LAB_METRICS_ENABLED=1` **before startup** (with `APP_API_ENABLED=1` and the ordinary independent read/write tokens). This is a startup flag, not a mutable endpoint; turn it off by restarting the service without it. In the example above omit the variable entirely to get the disabled baseline.

When opted in, each request must include `X-Deroyque-Load-Run: <run-id>` and `externalId` must start with `<run-id>-`. Authenticated metrics retrieval:

```sh
DEROYQUE_SIM_READ_TOKEN=<read-token-from-demo-credentials> \
  python3 simulator/server_metrics.py --url http://127.0.0.1:18081 \
  --run-id <run-id> --output /tmp/deroyque-run-metrics.json
```

Use the same service instance for the whole run. The snapshot contains `instance_id` so a restarted JVM cannot be silently represented as the same measurement session. The data is in-memory and resets on restart; it is not an event audit log. At most 128 tagged run IDs are retained, with atomic capacity checks; further runs are not captured. Do not enable it for production.

## Measurement overhead and limits

The opt-in collector adds atomic counters, memory allocations per run, request timing calls (`System.nanoTime`) and work on the HTTP handler. Measurements can distort performance, especially at high contention. **Benchmark with and without the flag**; never claim the instrumented throughput equals the uninstrumented baseline. Captured counts are for admission HTTP handlers, not RabbitMQ delivery or external provider completion. The monotonic clock is local to one service JVM, and a cluster with multiple service JVMs needs a separate authenticated snapshot from each and an appropriate aggregation method. Successful HTTP response writes may still be ambiguous to a disconnected client. The laboratory endpoint requires the read token and binds only to loopback.

## Offline two-coordinator audit

Generate shards using `simulator/distributed_prepare.py` or run the same global plan with `--shard-count 2`, `--shard-index 0/1` and identical `--run-id`. After all hosts finish, capture server metrics, then use:

```sh
python3 simulator/distributed_merge.py --server-metrics /tmp/deroyque-run-metrics.json \
  simulator/results/<run-id>-host-0 simulator/results/<run-id>-host-1
```

The merger rejects missing shards, conflicting plans, forged sequence identities, mismatched server status counts, incomplete writes and invalid monotonic windows. Without opt-in metrics the merger still audits idempotency and omits cross-host throughput.

**No second physical Linux is provisioned by this milestone.** Two processes on a single host validate protocol behavior, not independence of physical compute resources.

For the executed results and disclosed integration failure, see [Milestone 08 audit](MILESTONE-08-AUDIT.md).

For the executed results and disclosed integration failure, see [Milestone 08 audit](MILESTONE-08-AUDIT.md).
