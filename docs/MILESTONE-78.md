# Milestone 78 — Actual two-process clock-probe and collector integration

The `test_clock_probe_live.py` integration test launches a disposable HTTP
clock reference bound to the Linux loopback interface. Two independently
forked Python probe processes make eight genuine HTTP round-trip observations
each, and the collector receives their files in deliberately reversed shard
order. It must restore canonical shard ordering and produce bounded interval
evidence, while retaining its `BOUNDED_SELF_REPORTED_NOT_AUTHENTICATED` status.

A socket-readiness gate avoids mistaking a scheduler-dependent startup delay
for clock-service failure. The test cannot claim physically independent hosts,
authenticated endpoints, service-side latency or synchronized NTP clocks.
