# Milestone 131 — Recorded collector time and policy for preflight evidence

`host_matrix.audit` now records the exact `collector_checked_at_epoch_ms` used for strict temporal validation, the `max_age_ms` / `max_future_ms` budgets and the `network_required` gate. This allows a later auditor to *replay the original declared policy*, rather than silently using its present wall clock or today's defaults. These fields are not an independently trusted timestamp; a dishonest collector can backdate a local file.
