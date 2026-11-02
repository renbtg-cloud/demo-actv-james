# Milestone 88 — No unsigned clock aggregation substitution

The merger now requires that every offset sample used in an aggregated clock
analysis match the corresponding independently signed per-host clock report.
An unsigned aggregate with different samples is rejected even if its individual
values pass the mathematical four-timestamp interval checks. Its reference ID
must agree across all attached reports. Signed clock mode requires the actual
clock aggregate to be supplied; reporting a successful unattached clock is
not permitted.

This establishes byte accountability across artifacts, not trusted NTP or
physical node isolation.
