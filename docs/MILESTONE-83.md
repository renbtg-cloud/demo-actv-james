# Milestone 83 — Fail-closed integration into distributed merge and CI

The distributed result merger accepts an independently retained host-key
inventory and a signed report for every shard. Supplying only one half of
the evidence fails; `--require-host-signatures` requires both. Successful
verification is recorded explicitly as
`ALL_PINNED_SIGNING_KEYS_VERIFIED_NOT_HOST_ATTESTATION`.

The simulator CI installs Python cryptography and executes real signature
creation, direct verification, tamper/replay and merge-integration tests.
The feature is optional for legacy local tests and can be combined with
launch, SHA-256 index, clock and reported-kernel strict preflight. No
independent physical hosts or trusted hardware attestation are claimed.
