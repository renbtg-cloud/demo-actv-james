# Milestone 67 — Interval-censored terminal convergence

First observed admission and all-DONE are both results of polling. Their apparent
difference cannot be called the true duration: subtracting two upper timestamps
can either overestimate or underestimate real convergence. We now retain the
last zero-admission timestamp and last incomplete-DONE timestamp and compute a
nonnegative conservative lower bound and an upper bound on convergence from a
single monotonic clock. If first admission and all-DONE occur in the same poll,
the interval starts at zero rather than inventing a negative duration.

All quantities describe PostgreSQL visibility, not supplier effects, wall-clock
synchronization or per-identity latency. Strict unit tests include irregular gaps.

Timing conservatism: PostgreSQL snapshots can occur at any point inside the query round trip. The trace therefore records SQL query start and completion timestamps; the lower-bound evidence uses the previous query start, and upper-bound evidence uses the first positive/complete query end. Both the longest query round trip and sampling gap are disclosed.
