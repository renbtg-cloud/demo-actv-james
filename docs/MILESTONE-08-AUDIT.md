# Milestone 08 — integration and evidence audit

## Observed outcome (2026-11-02, same Linux)

**Final integrated run:** **0/320 unexpected HTTP/transport failures; 320/320 expected HTTP outcomes.**

- Two independent Python coordinator processes and **8 Java 21 producer JVMs** (4 per coordinator).
- Real Java Deroyque service, PostgreSQL 17.11, RabbitMQ 4.4.0, independent SQLite-backed HTTP mock provider.
- **80 HTTP 202, 169 HTTP 200 and 71 HTTP 409**; 320 POSTs in total.
- Global audit: **0 idempotency violations, 0 harness errors, 0 state verification errors**; **80/80 unique activation requests at `DONE`** after reconciliation.
- Optional service-side collection: **106.231 POST/s** over **3.012300 s** measured by **one** service JVM monotonic clock, **36 peak service HTTP handlers**.
- API authorization: no token → HTTP 401; write token → HTTP 401; read token → HTTP 200 for opt-in metrics route.
- With instrumentation **OFF by default**, test GET before and after tagged POST → HTTP 404, tagged POST itself → HTTP 202.
- Java adapter regression: **0/37 failures; 37/37 passed**, including concurrent 256 distinct run-ID registrations capped at 128.
- Input simulator regression: **0/52 failures; 52/52 passed**.

The initial integration attempt generated 320/320 expected POST responses but **failed the metrics collection step with HTTP 404**, because `P13Service` had not routed the metrics path to `RestApi`. This was corrected and the complete integration was repeated; the successful results above are from the **second** run. We do not misrepresent the first run as fully passing.

## Explicit defaults and safeguards

- `APP_LAB_METRICS_ENABLED` is an opt-in startup setting; unset/`0` leaves collection off, including in `scripts/dev_demo.py`.
- When disabled, no `IngressMeasurements` instance is created, and there are no per-request counter/timestamp updates.
- When enabled, collection allocates per-run atomics and uses request `System.nanoTime()`; overhead may change benchmark performance. **Not yet quantified by controlled A/B benchmarking.**
- Only a bearer read token can retrieve snapshots. Measurements are in-memory and reset on JVM restart. Capture for a **single JVM instance**; never merge snapshots from different service processes as if they were one common timebase.
- The 128-run cap is strict under concurrent first registrations. Once full, additional new run IDs are ignored for the collector, so their measurements are *not* complete; fail-closed global audit catches mismatched counts.
- A valid server-side admission metric does not prove remotely durable or exactly-once business effects. The mock receipt contract is not a substitute for trust in a real provider.
- This is **one Linux host**, not a multi-machine benchmark. The peak 36 is **HTTP handlers**, not distinct producer JVMs or peak network in-flight across hosts.

## Reproduction and evidence

- `docs/MILESTONE-08.md`: opt-in command, full scope and limitations.
- `scripts/verify-two-coordinators.py`: exercise two local coordinator processes with shared idempotency keys, capture a tagged service snapshot and verify it in the global merger. Requires explicit evaluator-enabled instrumentation.
- `simulator/results/m8-real-two-coordinators-02/`: two raw coordinator logs, per-shard outputs, server snapshot and consolidated `merged.json` (no credentials).
- `scripts/test-adapter.sh`: adapter contracts, default-off, authentication and strict cardinality guard.
- `python3 -m unittest discover -s simulator/tests`: simulator/distributed merger tests, including incomplete and forged snapshots.
