# Milestone 90 — End-to-end collector CLI and simulator CI acceptance

The collector run contract, signer CLI, verifier CLI, strict distributed merger,
clock samples and durable ledger are exercised as actual subprocesses with
real Ed25519 keys. A separate CI job runs the collector security tests.
Adversarial controls include host/shard swaps, stale challenge, unsigned report,
corrupt evidence and replay. No remote infrastructure is impersonated as real
separate hosts for these tests.
