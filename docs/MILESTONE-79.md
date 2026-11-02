# Milestone 79 — Fail-closed clock evidence under outage or alteration

The real clock-probe integration test forcibly stops the reference server
before a probe is issued: the client must return nonzero and leave no report
file behind. A separate adversarial test alters one of the remote timestamps
inside a previously valid sample and requires the collector to reject the
inconsistent interval rather than manufacture a safe clock bound. Nonloopback
reference URLs are rejected before a request is made.

These tests address local harness faults. A hostile remote host can still forge
self-reported timestamps and kernel metadata; cryptographic host authentication
and independent host-control evidence remain out of scope.
