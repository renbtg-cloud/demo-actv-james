# Milestone 145 — Real collector key-pair and hostile checkpoint CI regressions

`simulator/tests/test_readiness_checkpoint.py` tests genuine Ed25519 host and collector signing keys, actual signing and verification CLI subprocesses, global merge integration, one-time result publication, altered bytes/policy/session, key substitution, signature bit flips, duplicate keys, FIFO/symlink/oversize inputs and an intentionally corrupted post-run signed report. The dedicated collector signing GitHub Actions job runs the tests and must pass before claiming remote validation.
