# Milestone 70 — Randomized independent timing oracle

An independent deterministic synthetic time model now generates 600 distinct
first-admission and terminal-DONE instants, followed by simulated nonoverlapping
SQL query intervals with an independent snapshot timestamp hidden from the
actual observer. It verifies that every reported lower/upper convergence bound
contains the true (oracle-known) duration, within 1 microsecond of rounding.

The oracle also requires cases where simply subtracting two observed poll
completion timestamps overestimates and underestimates the true duration.
This is a *mathematical regression* against biased timestamp arithmetic,
not evidence of provider durability, production latency or multi-host behavior.
The real Spring gateway CI repeats this unit contract as part of the simulator
suite and separately verifies the live PostgreSQL-backed trace.
