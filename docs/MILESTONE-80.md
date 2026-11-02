# Milestone 80 — Independent 512-case clock-interval arithmetic oracle

The deterministic oracle constructs 512 independent offset scenarios with
randomized positive one-way transit and remote processing delays. Unlike the
production auditor, it retains the synthetic true offset and asserts that both
calculated conservative bounds contain it in every scenario. It also constructs
mutually contradictory offset samples, which must fail closed.

The oracle tests the interval mathematics under its documented assumptions.
It does not measure Internet delay, attest remote host identity or justify
a cross-machine throughput figure. A green local result must not be presented
as full GitHub CI acceptance before the remote matrix passes.
