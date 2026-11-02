# Milestone 71 — Race-safe gateway accounting regression

A full 104-test GitHub simulator suite discovered a transient failure in the
transport-error test: the HTTP client had already received its 502 while the
witness was still finishing **downstream-delivery accounting** on its request
thread. The gateway intentionally keeps the request in flight until delivery
is attempted, and that behavior must not be weakened to satisfy a test.

The regression now waits a bounded three seconds for the observer's own
`finished == 1 && inflight == 0` transition and only then asserts exactly one
transport error and rejection by the independent witness auditor. A timeout
remains a hard failure, not a skipped assertion. This is a test synchronization
fix, not evidence of improved service availability or throughput.
