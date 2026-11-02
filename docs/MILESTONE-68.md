# Milestone 68 — Observation coverage / resolution gates

The observer exposes largest actual inter-sample gap and overall bound width.
The real Spring lab gate rejects bound widths and individual sampling gaps above
12 seconds for the short workload and 20 seconds for the 120-second workload.
These high thresholds avoid confusing ordinary poll jitter with a guarantee of
millisecond precision, yet prevent seriously interrupted observers from silently
passing. The expected cadence is 0.5/1.0 seconds, not a claimed upper bound.

Raw DB counters are checked for monotonicity and physical cardinality, SQL
query failures propagate, and credentials/identity-level payloads are not logged.

Timing conservatism: PostgreSQL snapshots can occur at any point inside the query round trip. The trace therefore records SQL query start and completion timestamps; the lower-bound evidence uses the previous query start, and upper-bound evidence uses the first positive/complete query end. Both the longest query round trip and sampling gap are disclosed.
