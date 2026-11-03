# Milestone 142 — Independently rerun signed preflight before signing or trusting a receipt

Both signing and verification first reproduce the strict-temporal signed preflight against its exact reports, per-host Ed25519 proof files, collector-held pins, launch manifest, challenge and recorded time/policy budgets. An altered field is rejected before a collector signature can legitimize an invalid preflight. A second read must match the previously reconstructed JSON, reducing read/sign and read/verify race exposure.
