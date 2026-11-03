# Milestone 127 — Bounded hostile proof and private-key input

Readiness envelopes are capped at 16 KiB, signed report files at 128 KiB and private Ed25519 keys at 128 KiB. Open-descriptor checks reject pipes, devices, final symlinks, file length changes and observable in-place modifications during intake. Owner-only private-key mode is required. Duplicate JSON object fields, non-finite JSON values, noncanonical base64 and malformed signature sizes fail closed. As with prior evidence import, secure parent-directory custody and hardware trust remain outside the process.
