# Milestone 15 — crash-window acceptance on actual Spring Boot

This milestone adds a **separate GitHub Actions matrix job** exercising three
deterministic `Runtime.halt` boundaries in real Spring Boot, with independently
committed state and a real PostgreSQL / RabbitMQ / SQLite-backed provider.

- After durable authorization (`P13_CRASH_AFTER_AUTH_ONCE`, exit 81):
  request is `IN_FLIGHT` and has one dispatch but no durable observation or provider effect.
- After provider HTTP response (`P13_CRASH_AFTER_HTTP_ONCE`, exit 82):
  one provider effect exists; no signed observation is durable yet.
- After signed observation but before settlement (`P14_CRASH_AFTER_OBSERVE_ONCE`, exit 83):
  one signed observation and one effect exist; settlement has not yet committed.

Every case runs against a **new Flyway-migrated schema** and unique RabbitMQ queue,
with one deliberately fault-armed Spring process owning the initial delivery.
The test checks the crash marker and exact JVM exit code and inspects persistent
PostgreSQL/SQLite state *before* launching a different Spring JVM to recover.
It subsequently demands `DONE`, exactly one durable external effect, signed
evidence, settlement and same-UUID idempotent API replay. In the post-observation
case it also demands **no additional POST** to the provider during recovery.

The faulted service intentionally runs alone at the point of crash to make
ownership deterministic. This is **process replacement**, not proof of
concurrent competing recovery; the independent existing `staggered-crashes`
GitHub Actions matrix job covers concurrent peer competition and two staggered
`SIGKILL`s with 24 actual HTTP-overlapping producer JVMs. These are complementary
experiments; neither alone proves generic exactly-once remote effects.

The provider fixture is signed for authenticity in a laboratory only. Its
idempotency retention is explicitly set to 600 seconds, while the service
assumes 300 seconds. The window outside that finite retention is **not** covered.

**Evidence:** `.github/workflows/spring-real-integration.yml` and its individual
`exact-fault-windows` and `staggered-crashes` matrix job logs. Counts and final
result must come from a completed CI run, not from source-only checks.
