# Milestone 11 — service transport separation and concurrent regression

**Delivery: integrated Java engine refactor validated; Spring Boot runtime remains unverified.** This milestone advances the same service rather than introducing an independent POC.

## Runtime structure

- `runtime-rabbit/.../ProviderHttpGateway.java`: actual Java 21 `HttpClient` transport for the provider activation POST and provider lookup GET. It encapsulates URI construction, form/query encoding, per-call deadlines, receipt/attestation headers and unique signed-query challenge generation. It does **not** decide whether a signed assertion is true or perform business settlement. Transport failures or no lookup return **no evidence of absence**.
- `runtime-rabbit/.../RabbitDelivery.java`: actual RabbitMQ quorum-queue consumer lifecycle, channel recovery, prefetch, manual ACK, publisher confirms, return handling and durable outbox publication. It delegates the business work callback to `P13Service` and uses the ledger's durable reserve/fence/mark transitions. Fault injection for lost ACK and forgotten publisher confirm is retained.
- `P13Service.java`: coordinates durable authorization, preflight, observation, recovery, settlement, policy and lifecycle; no longer constructs provider `HttpRequest` objects or directly owns RabbitMQ `Channel`/`Connection` objects. It still contains substantial durable workflow logic and is **not** fully decomposed.
- The JDK HTTP adapter remains the *executed* API. The Spring MVC adapter still has source, `pom.xml` and test-only source signature checks. A **genuine Spring runtime was not built or launched** because Maven and Spring artifacts are unavailable in this container and DNS to Maven Central is unavailable.

## Verification executed against the changed code

| Suite | Failed/total | Passed/total | Scope |
| --- | ---: | ---: | --- |
| JDK API adapter | 0/37 | 37/37 | Authorization, admission, metrics opt-in, error handling |
| Framework-neutral HTTP facade | 0/33 | 33/33 | Concurrent admissions, mapping, bounded payloads |
| Spring source boundary | 0/9 | 9/9 | **Test-only Spring API stand-ins**, not real Spring runtime |
| New provider HTTP transport | 0/18 | 18/18 | JDK HttpServer socket tests, encoding, receipts, nonces, failure |
| Headless engine | 0/8 | 8/8 | Real PostgreSQL 17.11, RabbitMQ 4.4.0, SQLite provider, no HTTP listener |
| End-to-end | 0/53 | 53/53 | Real PostgreSQL, RabbitMQ, SQLite provider, JVM crash and signed proof |
| Input simulator | 0/52 | 52/52 | Simulator regression tests |
| **Total** | **0/210** | **210/210** | Assertion counts; not 210 independent properties |

The live multi-JVM admission run, using the **actual newly refactored JARs**, returned **0/480 unexpected HTTP outcomes**; **480/480** results were expected. Resource-aware auto-sizing selected **11 separate producer JVMs**, all observed with in-flight HTTP at the same time, and recorded a peak of 66 in-flight HTTPs. **120/120 unique activations reached `DONE`** within the configured verification window, with no detected audit violations. Observed admission throughput was 108.23 POST/s and p95 HTTP latency about 999.8 ms. These numbers are specific to the current shared 4-CPU/4-GiB Linux and **do not** establish a production throughput ceiling. The input generator and service used the same Linux.

## How to reproduce

```sh
./scripts/build.sh
./scripts/test-adapter.sh
./scripts/test-http-facade.sh
./scripts/test-spring-source-boundary.sh   # test doubles, not Spring Boot
./scripts/test-provider-transport.sh       # actual socket/JDK HttpClient
python3 -m unittest discover -s simulator/tests -v
python3 -u tests/test_headless_engine.py   # requires local PG/RabbitMQ
export DEROYQUE_TEST_CP="$(./scripts/print-classpath.sh)"
python3 -u tests/run_end_to_end.py         # requires local PG/RabbitMQ
python3 -u tests/run_m11_concurrent_ingress.py  # isolated demo & 11-ish JVM auto load
```

All real-infrastructure tests create fresh PostgreSQL schemas and RabbitMQ queues. The ingress harness creates a disposable service and independent SQLite HTTP provider and removes its temporary read/write credential file; it does not shut down shared PostgreSQL/RabbitMQ services. The private console log from the demo may contain laboratory tokens and **must not be distributed**. The packaged `results/m11/` contains *only* test outcomes and an aggregate load report without tokens.

## Remaining acceptance gates

1. Compile and run the **actual** Spring Boot 4.1.2 application with Maven-resolved dependencies, apply Flyway automatically, exercise the true MVC routes, and stress its lifecycle against real PostgreSQL/RabbitMQ/SQLite. The Spring source stand-ins are not a substitute.
2. Continue decomposing durable worker decisions and SQL into clear application/port adapters; preserve fencing/uncertain-commit semantics.
3. Replace static laboratory bearer tokens with a verified production security design (JWT scopes, issuer/audience, TLS, tenant boundaries, secret management). Instrumentation remains **OFF by default**, opt-in for the evaluator.
4. Test inter-host isolation, multi-node broker and database failure modes, and provider contracts independently. Signed testimony is not proof that the provider's statement is true, nor do we promise global exactly-once external effects.
