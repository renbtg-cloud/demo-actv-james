# Milestone 74 — Conservative clock-offset intervals

`simulator/clock_bounds.py` audits three or more four-timestamp probes per shard. With nonnegative one-way transit, stable clock rates during the exchange and local monotonic/wall clock agreement, each probe bounds remote-minus-local UTC offset in `[remote_receive - local_receive, remote_send - local_send]`. Intersect the intervals; refuse inconsistency or an uncertainty width over the configured tolerance.

The result *never* fabricates cross-host peak concurrency or throughput from incomparable monotonic clocks, and deliberately reports cross-host wall-time QPS as unknown. Offsets bounded for brief probes may drift after probing. It does not authenticate an NTP service, establish physical isolation or turn untrusted producer timing into ground truth.
