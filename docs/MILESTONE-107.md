# Milestone 107 — Linux runtime and resource preflight

The preflight actually invokes `java -version` and requires Java 21+, checks
Python 3.12+, the Linux boot fingerprint, effective CPU affinity, available
results-disk space and cgroup memory when readable. It rejects an aggregate
configured JVM heap larger than the cgroup limit. All resource values are a
point-in-time snapshot: they **cannot** guarantee performance or prevent a
later out-of-memory event, especially since native JVM memory is not included.
