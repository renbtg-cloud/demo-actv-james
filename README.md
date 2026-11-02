# Deroyque / demoactv — experimental distributed activation engine

> **Asynchronous fault-tolerant product activation**

This repository contains the **actual experimental source code**, not the older documentation-only preview. The three primary deliverables are the asynchronous activation service, an independent HTTP provider simulator, and a configurable multi-JVM incoming-transaction simulator. They are developed as one evolving system.

**Verified baseline (2 November 2026, Milestone 50):** all 15 GitHub Actions
jobs succeeded, including the 120-second two-Spring-JVM gateway load experiment
(**0/20 failures; 20/20 checks passed**) and three genuinely isolated sequential
real-Spring trials (**0/15 failures; 15/15 passed**). See
[GitHub Actions run 41515165482](https://github.com/renbtg-cloud/demo-actv-dryqe/actions/runs/41515165482).
Milestones 51–53 below add stronger per-identity proofs and require fresh CI
verification before being treated as accepted.

## Milestones 39–43 — One-clock ingress witness and real distributed-workload verification

An **optional loopback-only HTTP gateway observer** now distributes admissions to
separate backend listeners and measures response completion through **one monotonic
clock**. Global reconciliation fails closed if its counts, HTTP statuses, timing,
transport errors or per-backend totals contradict the independent Java producer
results. This does not measure provider-completed effects, remove gateway overhead,
or imply physical isolation. The real Spring CI scenario independently audits
PostgreSQL/RabbitMQ/SQLite effects, two Spring JVM counters and four real Java
producer processes. **Real integration status is determined only by its GitHub
Actions job**, not the local fake-backend regression. See `docs/MILESTONE-39.md`
through `docs/MILESTONE-43.md`.

## Milestones 44–46 — Delivery, latency and repeated-trial honesty

Gateway evidence now distinguishes *upstream responses* from best-effort
*downstream socket delivery*, rejects observed delivery failures, and provides
a bounded fixed-bin round-trip histogram whose counters must reconcile.
`simulator/trial_compare.py` rejects incomparable workloads or missing terminal
states and reports only descriptive rates across three or more completed
independent trials. The current repository does **not** claim to have completed
a long-duration production-grade benchmarking campaign. See
`docs/MILESTONE-44.md` through `docs/MILESTONE-46.md`.

## Milestones 47–48 — Race-free measurement closure and cross-store proof

The witness now keeps an HTTP admission in flight through the **downstream
socket-delivery attempt**, even though its measured latency stops when the
upstream reply is read. A concurrent snapshot cannot falsely appear complete
while a client send is blocked. The real Spring CI experiment also requires
agreement of 20 database requests, dispatches, settlements, signed attestations
and separately persisted provider effects. These are bounded scenario proofs,
not universal exactly-once guarantees. See `docs/MILESTONE-47.md` and
`docs/MILESTONE-48.md`.

## Milestones 49–50 — Sustained real Spring traffic and repeated trials

Two dedicated *real-infrastructure* CI scenarios expand the laboratory: 240
POSTs over approximately two minutes across two Spring JVMs and the separately
persisted provider, plus three independently initialized repeated experiments
with descriptive median/MAD. Database dispatches, settlements, signed evidence
and provider effects are explicitly reconciled per run. Neither the 120-second
load nor three hosted CI repetitions is advertised as endurance certification,
service-only saturation, independent hardware or causality proof. See
`docs/MILESTONE-49.md` and `docs/MILESTONE-50.md` for the acceptance gates;
**Both passed in GitHub Actions run 41515165482 (15/15 jobs green).**

## Milestones 51–53 — Exact per-identity cross-store graph and independently reverified signatures

The clean real-Spring gateway workloads now require a **per-identity bijection**:
each expected external ID must own exactly one durable request, dispatch,
settlement, correctly attributed signed proof and SQLite effect. An independent
Python cryptographic verifier then checks the stored Ed25519 token against the
issuer's laboratory public key, expected key, nonce and signed metadata. Mutation
tests reject offsetting incorrect keys and distorted graphs even when aggregate
counts match. This is an experiment-specific normal-path assertion: retry-heavy
failure scenarios can legitimately create multiple dispatches. Signed proofs
establish authenticity, never truthfulness of a dishonest supplier.
See `docs/MILESTONE-51.md` through `docs/MILESTONE-53.md`. Real CI acceptance is
pending the new workflow run.

## Milestones 54–60 — Full-chain, independently falsifiable provider evidence

The actual two-Spring-JVM gateway experiments now check each identity's terminal
PostgreSQL stage and `DONE` settlement, the attestation's *observation/dispatch*
lineage, schema-wide absence of unaccounted dispatches/settlements/proofs in the
fresh test schema, and the independent SQLite provider's idempotency index as
well as its effect journal. Ed25519-verified timestamps are checked against the
provider effect creation/expiration interval **only because this lab uses one
host clock**. A negative-mutation suite and the fail-closed CI gate require
these stronger proofs for short, sustained and repeated real experiments.

These are **test-fixture-specific invariants**, not claims that production
services never retry, reject a proof, or dispatch a request more than once.
Never interpret signed evidence as proof that a dishonest external provider
is truthful. See `docs/MILESTONE-54.md` through `docs/MILESTONE-60.md`.
The results of a new CI run, not local tests alone, determine real acceptance.

## Milestones 61–63 — Race-aware immutable payload and fingerprint proof

The global producer auditor now reports the *observed* winning payload variant
for every unique idempotency identity. The test does not infer a winner from
scheduled request order: independent JVMs can reorder HTTP arrival. The
PostgreSQL auditor then independently verifies the stored customer/product
fields against the winning variant and recomputes the exact Java SHA-256
fingerprint in Python. Missing, swapped, overwritten or falsely fingerprinted
payloads fail the new `payload_contract_verified` CI gate. See
`docs/MILESTONE-61.md` through `docs/MILESTONE-63.md`. These are isolated
laboratory checks, not universal production payload semantics.

## Layout

- `core/`: admission and evidence contracts, policy and safe parsing.
- `store-postgres/`: durable PostgreSQL state, authorization, outbox and evidence.
- `runtime-rabbit/`: worker engine, RabbitMQ transport, HTTP provider transport and reconciliation.
- `adapter-springboot/`: Spring MVC application and Flyway migrations.
- `adapter-httpjdk/`: earlier independently verified HTTP transport (regression control, not the target deployment).
- `provider/`: independent SQLite-backed HTTP provider simulator.
- `simulator/`: multi-JVM input generator, profiles, coordinated shards, saturation explorer and comparison tools.
- `tests/`: offline and real-infrastructure regression and acceptance tools.
- `.github/workflows/spring-real-integration.yml`: builds and runs the actual Spring Boot integration test on a hosted runner with PostgreSQL and RabbitMQ services.

## Keycloak identity-provider integration (Milestone 19)

A new GitHub Actions matrix job (`oidc-keycloak`) tests the **real Spring OAuth2 Resource Server** against a disposable Keycloak authorization server. It obtains access tokens from the genuine `client_credentials` endpoint using per-run clients and realms, exercises issuer/audience/scope authorization, then verifies the durable activation through PostgreSQL, RabbitMQ and an independent SQLite provider. The real OIDC job passed **0/26 failures; 26/26 checks passed**. Combined with the seven prior real-Spring jobs, [GitHub Actions run 41469917139](https://github.com/renbtg-cloud/demo-actv-dryqe/actions/runs/41469917139) passed **0/238 failures; 238/238 checks passed**. See `docs/MILESTONE-19.md`. This is a CI-only local HTTP development server; **it is not a production IdP deployment**.


## Milestone 23 — Strict Unicode admission

Malformed UTF-8 byte sequences and unmatched UTF-16 surrogate escapes now
return 400 rather than becoming a replacement-character identity. Valid emoji
surrogate pairs remain valid JSON strings. See [Milestone 23](docs/MILESTONE-23.md).

## Milestone 24 — Explicit JSON charset contract

Only UTF-8 JSON is supported; contradictory/malformed charset parameters and
non-UTF-8 declarations return 415 before any database writes. See
[Milestone 24](docs/MILESTONE-24.md).

## Milestone 32 — Independent consumer verification of published ZIP

A **separate CI runner**, dependent on successful source publication, downloads
the uploaded artifact and compares every file byte against a fresh checkout of
the same commit, in addition to validating its internal manifest. This catches
self-consistent but wrong ZIP contents and accidental upload/download changes.
See [Milestone 32](docs/MILESTONE-32.md).

## Milestone 31 — Git-tracked-only reproducible source artifact

A separate GitHub Actions job now builds a deterministic ZIP using **only Git-
tracked files**, embeds SHA-256 manifests, verifies every entry and publishes
a short-retention source artifact. Untracked `.local` secrets, SQLite files and
runner scratch data cannot enter via directory globs. The local packaging
regression checks byte reproducibility, corruption and path-traversal cases.
See [Milestone 31](docs/MILESTONE-31.md).

## Milestone 30 — Fail-closed CI evidence audit

After each real integration job, a separate validator checks the JSON evidence
file for a complete positive assertion list, zero exit code, scenario-specific
proof flags and minimum baseline coverage. Jobs fail if the evidence is empty,
inconsistent or does not prove external effects where required. Sanitized
counts are added to the GitHub Actions job summary. See
[Milestone 30](docs/MILESTONE-30.md).

## Milestone 29 — Cross-language differential JSON acceptance

A separate Java process running the actual `StrictJson` parser is now checked
against Python's independent `json` decoder with strict duplicate-key and
Unicode-scalar policy. **1,500 synthetic cases** passed locally; the GitHub
JWT integration job repeats the comparison. See [Milestone 29](docs/MILESTONE-29.md).

## Milestone 28 — Seeded Unicode parser property testing

An independent deterministic Java contract runs **3,612 adversarial assertions**
against raw/escaped Unicode equivalence, lone surrogates and escaped duplicate
object keys. The `jwt-security` CI job now executes it before starting Spring.
This strengthens evidence for the intentionally small JSON parser, not proof
that all JSON syntax or encodings are supported. See [Milestone 28](docs/MILESTONE-28.md).

## Milestone 27 — Reliable CI bootstrap on ephemeral Ubuntu runners

A failed Keycloak CI job on GitHub-hosted Ubuntu 26.04 traced to a **third-party
Microsoft apt source without a Release file**, before any Deroyque code ran.
The workflow now disables only source files containing that unused endpoint
on the disposable runner; `apt-get update` still fails for essential repository
errors. See [Milestone 27](docs/MILESTONE-27.md).

## Milestone 26 — Cross-encoding Unicode idempotency

A real Spring/SQLite-provider test proves an escaped surrogate pair and an
identical literal UTF-8 scalar converge to the same durable external ID, UUID
and single external effect; escaped duplicate object keys are rejected.
See [Milestone 26](docs/MILESTONE-26.md).

## Milestone 25 — Reject-before-side-effect HTTP regression

The real Spring JWT acceptance harness verifies malformed input and denied
routes/scope/method combinations cause no PostgreSQL admissions, including 24
concurrent invalid requests, before the known-good signed-provider activation.
See [Milestone 25](docs/MILESTONE-25.md).

## Milestone 21 — JWKS key rotation and issuer-outage security tests (verified)

The existing `jwt-security` integration harness now exercises an actual mutable
loopback JWKS endpoint: new RS256 key discovery without restarting Spring,
issuer outage while an unfamiliar key is presented, recovery, an overlap period,
concurrent signed requests, and issuer/audience denial after rotation.
This was verified with 0/31 failed integration assertions; 31/31 passed in the JWT CI job.
The script intentionally **does not claim immediate token revocation** when a
public key disappears from JWKS: resource-server JWKS caches may retain keys
until their TTL expires. No production key rotation is configured by this
fixture. See `docs/MILESTONE-21.md` for the test contract and verification gate.

## Milestone 22 — JWT attack vectors and bounded access-token lifetime (verified)

Spring's real JWT resource server is tested against unsigned `alg=none`,
RSA-public-material `HS256` confusion, genuinely signed but unapproved `RS512`,
attacker-controlled `jku` and embedded `jwk` headers. The `jku` test explicitly
checks that the attacker-controlled URL was **not fetched**. Tokens with missing
`iat`, `exp` or `sub`, nonpositive lifetimes, future issuance beyond 60 seconds,
or an excessive (`exp - iat`) lifespan must fail HTTP 401. A small positive
issuance skew remains permitted. The real Keycloak CI jobs remain in the matrix.

`APP_JWT_MAX_LIFETIME_SECONDS` defaults to **3600** and may be set to an integer
between **60 and 86400**. Tokens exceeding this limit are rejected even if the
issuer signs them. This is an intentional compatibility/security choice: align
it with the authorization server's actual access-token TTL; do not use it for
refresh tokens. Missing/malformed configuration fails startup. Key rollover
still does not provide instantaneous revocation of cached old signing keys.
See [`docs/MILESTONE-22.md`](docs/MILESTONE-22.md).

## Build and test the real Spring Boot service

Requirements: Java 21, Maven with dependency-repository access, Python 3.12+, `psql`, PostgreSQL 17, RabbitMQ 4.4, and the Python package `cryptography` for the independent test provider.

```bash
./scripts/test-spring-real.sh --output /tmp/deroyque-spring-acceptance.json
```

The harness creates a **unique temporary PostgreSQL schema** and a uniquely named RabbitMQ queue, requires Spring to execute Flyway migrations on the empty schema, launches the independently persisted HTTP provider, checks admission/idempotency/reconciliation, and drives additional admissions through the first-class multi-JVM simulator. It does not use Spring substitutes in this mode.

The GitHub Actions workflow invokes this command and provides PostgreSQL/RabbitMQ. A CI failure is evidence of an unresolved integration issue; do not treat the old JDK-server results as a substitute.

## Offline regression and diagnostics

```bash
./scripts/build.sh
./scripts/test-http-facade.sh
python3 -m unittest discover -s simulator/tests -v
```

The simulator supports CLI and YAML workload profiles, load generators in independent JVMs, deterministic sharding across hosts, saturating step tests and thread/JFR diagnostics. The optional **intra-service admission instrumentation is disabled by default** (`APP_LAB_METRICS_ENABLED=1` to opt in). All throughput figures obtained on a shared Linux are experimental; they do not establish standalone service capacity.

## Optional ready-to-use local Keycloak (Milestone 20)

**No existing Keycloak server is required** to try the OAuth2/OIDC integration. Docker is
needed only if you opt in. On the same machine where Deroyque is running:

```bash
python3 scripts/local_keycloak.py up
# On the same shell where the Spring Boot app will be launched:
eval "$(python3 scripts/local_keycloak.py env --provider local)"
# Start Deroyque with a working PostgreSQL/RabbitMQ and its normal JDBC/provider settings:
mvn -B -ntp -f adapter-springboot/pom.xml package
java -jar adapter-springboot/target/deroyque-springboot-0.10.0-SNAPSHOT.jar
```

The local issuer defaults to `http://127.0.0.1:18090/realms/deroyque-local`. The
application remains in **JWT mode**; `APP_JWT_ALLOW_HTTP_LOOPBACK=1` permits only
this **local** HTTP development provider. The realm has independent `reader`,
`writer` and `outsider` service-account clients, with actual Keycloak-generated
JWTs and real `activation:read` / `activation:write` scopes. Retrieve a short-lived
writer token to test a request without copying the client's secret:

```bash
TOKEN="$(python3 scripts/local_keycloak.py token --client writer)"
# Handle TOKEN as a credential: do not echo it or commit it.
python3 scripts/local_keycloak.py status
python3 scripts/local_keycloak.py down
unset TOKEN
```

To use your **own** OIDC-compatible identity provider instead, do not start local
Keycloak; configure the issuer/JWKS and audience explicitly (HTTPS is required):

```bash
eval "$(python3 scripts/local_keycloak.py env --provider external \
  --issuer 'https://idp.example/realms/your-realm' \
  --jwks-uri 'https://idp.example/realms/your-realm/protocol/openid-connect/certs' \
  --audience 'deroyque-activation-api')"
```

Both paths use the exact same Spring Security resource-server validation. The
local process and its disposable demonstration realm are **opt-in, off by default,
loopback-only, non-production**. Client credentials and realm files are generated
randomly under gitignored `.local/keycloak`, not committed. `down` removes the
container and temporary secrets. The local option passed **0/26 integration checks** with real Keycloak,
Spring Boot and the independent provider in GitHub Actions; all nine CI jobs
passed **0/264 failures**. See `docs/MILESTONE-20.md` for limitations,
troubleshooting, and [CI evidence](https://github.com/renbtg-cloud/demo-actv-dryqe/actions/runs/41475178125).

## Spring Security OAuth2/JWT authentication

The Spring Boot adapter now defaults to **JWT resource-server mode** (`APP_AUTH_MODE=jwt`). It will **fail startup without** `APP_JWT_ISSUER`, `APP_JWT_JWKS_URI` and `APP_JWT_AUDIENCE`. Tokens require a verifiable RS256 signature, matching issuer/audience, valid time claims, and the `activation:read` or `activation:write` scope for the matching endpoint. The application is only a resource server: use your trusted external identity provider or opt into the separate disposable local Keycloak described above. The main service never mints JWTs. Anonymous liveness/readiness are permitted; the laboratory ingress-metrics API is denied in JWT mode.

The old opaque bearer-token behavior is retained **only** when `APP_AUTH_MODE=lab` is explicitly selected. This is for local experiments and existing regression harnesses, not for deployment. JWKS over HTTP is rejected unless `APP_JWT_ALLOW_HTTP_LOOPBACK=1` and the URI targets loopback; this override is for explicitly enabled local development (including CI), never a remote HTTP identity provider.

## Security, guarantees and limitations

Do **not** deploy these test assets as a production service without further work. Laboratory bearer tokens, cryptographic fixtures and simulated provider receipts are for tests only. Exactly-once external side effects cannot be guaranteed against a provider that expires idempotency keys or lies about its effects. Consult `docs/STATUS-AND-GAPS.md` and the versioned milestone notes for demonstrated results, unresolved issues and the experimental history.


## Spring Boot multi-process adversarial CI

The GitHub Actions `Spring Boot real integration` workflow also runs an **opt-in**
second phase after the ordinary acceptance test. It starts a second real Spring Boot
JVM sharing the same PostgreSQL business state and RabbitMQ queue, starts a fresh
160-key / 960-POST workload using a memory-bounded number (maximum 24) of
**independent producer JVMs**, then kills the second Spring worker with `SIGKILL`
while admission is in progress. The surviving Spring JVM must complete the work.
The simulator audits HTTP outcomes, global idempotency, final states and producer
JVM overlap; the acceptance harness separately audits the provider SQLite effects.

The workload is **not** a performance benchmark of a production-sized instance.
A runner with insufficient memory fails the resource preflight rather than
silently reducing the test to two JVMs or disabling its memory safeguards.
The primary acceptance test is still runnable without the adversarial phase:

```bash
./scripts/test-spring-real.sh --load-jvms 8 --output /tmp/acceptance.json
```

To enable stress locally against isolated lab infrastructure and a working Maven
Central connection:

```bash
./scripts/test-spring-real.sh --adversarial-max-jvms 24 --output /tmp/adversarial.json
```

Crash injection does not prove the killed worker personally contacted the
provider before it died; it tests recovery from abrupt peer loss during a
concurrent workload. Idempotency guarantees still depend on the external
provider contract.


### Concurrent admission and crash-test configuration

`APP_ADMISSION_MAX_IN_FLIGHT` (default 32) bounds simultaneous HTTP admissions
per service JVM, reserving room for worker/database connections.
`APP_ADMISSION_WAIT_MS` (default 8000) is the maximum wait for an admission
slot; timeout responds 503 with an explicit same-key retry instruction.
This is admission backpressure, **not** optional observability.
`APP_LAB_DIAGNOSTICS_ENABLED=1` emits **only SQLState codes** for database
unavailability (OFF by default); no SQL, credentials, or request payloads.

The CI adversarial provider is explicitly configured for a finite 600-second
idempotency-key retention contract, with the application using a conservative
300-second authorization window. This is necessary for intentional JVM-crash
retries; retention=unknown would correctly pause uncertain attempts instead.
The simulator remains at 24 producer JVMs x 8 HTTP operations in the full
GitHub Actions stress plan; admission backpressure is exercised on the server.

## Milestone 14 — staggered multi-instance Spring failure test

GitHub Actions now runs **four truly independent Spring Boot JVM processes**
against the same PostgreSQL schema and RabbitMQ queue. A memory-aware number
of separate Java producer processes (up to 24) submits 960 real HTTP POSTs
sharing 160 idempotency keys. After observing durable admission and verified
worker activity on each victim, the harness issues two OS `SIGKILL` signals
to **different** Spring instances at distinct admission checkpoints; at least
two other Spring processes must remain alive. Auditors check all HTTP replies,
final states, overlapping producer JVMs, and exactly one SQLite provider
effect per accepted key. Workload thresholds, resource budget and crash
checkpoints are reported; runner capacity is not inferred.

Run in an isolated test environment:

```bash
./scripts/test-spring-real.sh --adversarial-max-jvms 24 --adversarial-service-jvms 4 --adversarial-crashes 2 --output /tmp/deroyque-m14.json
```

The incident tests survival of process loss during concurrent requests. It
**does not establish** that every killed JVM was between an external provider
side effect and local persistence at the instant of the kill. No claim of
unconditional external exactly-once delivery is made.

## Milestone 16 — Adversarial integration (CI)

The GitHub Actions matrix now includes real Spring Boot **competing recovery**, bounded PostgreSQL/RabbitMQ container interruptions and intentionally contradictory signed issuer claims. For methodology, failure semantics and precise limitations, see [Milestone 16](docs/MILESTONE-16.md). No success is claimed before the individual GitHub Actions jobs complete.


## Same-row contention acceptance (Milestone 17)

`tests/run_spring_same_row_race.py` drives four **independent real Spring Boot** consumer JVMs
onto a single PostgreSQL `READY` request while another PostgreSQL transaction holds
that **same row lock**. It publishes durable duplicate hints on RabbitMQ, records
which JVMs entered before lock release, then audits the winning commit, losing
claims, signed proof, settlement and independent provider effect. The new
`P21_TRACE_CLAIM_RACE=1` diagnostic is opt-in and does not gate production behavior.
The test seeds this specific internal request directly, without an outbox, so it
does **not** claim to re-test HTTP admission. See `docs/MILESTONE-17.md`.

Milestone 17 CI passed: 0/22 failures in the same-row case (22/22 passed),
0/189 failures across all six Spring Boot jobs (189/189 passed):
https://github.com/renbtg-cloud/demo-actv-dryqe/actions/runs/41451524902

The offline `scripts/build.sh` path additionally needs the included lab-only SLF4J
shim and two lab provider Java sources, plus separately provisioned PostgreSQL and
RabbitMQ client JAR dependencies. These external JARs are not bundled.

## Milestones 33–35 — Source-artifact trust boundaries

The deterministic CI source archive is now format **v2**, with Git executable-mode
integrity, a mandatory clean committed checkout and bounded, streaming ZIP
verification. The independent CI job compares both file bytes and executable
modes to a second Git checkout. The verifier rejects symlinks, ZIP bombs with
excessive advertised expansion, suspicious paths and malformed metadata.
See [M33](docs/MILESTONE-33.md), [M34](docs/MILESTONE-34.md) and
[M35](docs/MILESTONE-35.md). These changes require a new successful CI run;
passing offline regression tests alone does not establish CI success.

## Milestone 36 — Independent full-byte reproducibility

A second CI runner now rebuilds the Git-tracked source ZIP itself and requires a
**byte-identical** SHA-256 to the uploaded archive, in addition to the prior
content, permission and resource-budget checks. A ZIP with the correct source
files but modified wrapper metadata therefore fails. See
[Milestone 36](docs/MILESTONE-36.md). The GitHub workflow result is the
acceptance gate; locally passing tests are not a substitute.

## Milestones 36–38 — Evidence integrity and cross-instance measurement correctness

- **M36:** independently regenerate the entire canonical source ZIP and compare complete bytes, not just extracted contents. Changes to ZIP wrapper metadata cannot silently pass.
- **M37:** audit authenticated metrics from *every active service JVM* against all producer HTTP records; do not invent aggregate rate/peak across unsynchronized monotonic clocks. See [`docs/MILESTONE-37.md`](docs/MILESTONE-37.md).
- **M38:** record per-shard Linux kernel provenance without revealing raw boot IDs, and provide an opt-in fail-closed distinct-kernel evidence gate. Different self-reported boot hashes are **not proof of separate physical hosts**. See [`docs/MILESTONE-38.md`](docs/MILESTONE-38.md).

The M35 source-hardening baseline passed its [GitHub Actions CI run](https://github.com/renbtg-cloud/demo-actv-dryqe/actions/runs/41502346644). Changes after M35 require their own CI validation and must not be treated as verified merely because local tests passed.
