# Milestone 89 — Collector-local durable replay prevention

An optional SQLite collector journal accepts a successful, complete, signed
run exactly once using a transaction, a unique session fingerprint and a unique
run ID. Four real competing Python processes are tested to verify that only
one can publish the same result; later replays and inconsistent challenges fail.

This journal is a local collector publication gate, not a distributed consensus
service. Different independent collectors without a shared journal can still
accept the same proof. The journal must remain under the collector's control.
