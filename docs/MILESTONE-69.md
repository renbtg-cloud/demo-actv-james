# Milestone 69 — Independent CI evidence for timing intervals

The CI evidence gate requires finite, nonnegative, correctly ordered completion
bounds, a consistent reported bound width, a bounded largest sampling gap, and
an uncertainty no larger than that gap. Missing, NaN, infinite, inverted,
contradictory or low-resolution traces fail closed. For the three real Spring
trials, the comparator reports medians of lower bounds, observed differences,
and upper bounds. Median ordering must hold; these remain descriptive statistics
of same-host experiments, not confidence intervals or independent hardware.

Mutation tests construct false JSON evidence to prove the gate rejects it.

Timing conservatism: PostgreSQL snapshots can occur at any point inside the query round trip. The trace therefore records SQL query start and completion timestamps; the lower-bound evidence uses the previous query start, and upper-bound evidence uses the first positive/complete query end. Both the longest query round trip and sampling gap are disclosed.
