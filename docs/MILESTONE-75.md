# Milestone 75 — Controlled loopback clock probe and collection

`simulator/clock_probe.py` offers a loopback-only `/clock` reference server, an opt-in four-timestamp probe and a collector that validates run/reference identities for every shard. Remote probes require a separately established authenticated and encrypted tunnel to the collector's loopback listener. Direct plaintext remote URLs, redirects, credential-bearing URLs and unexpected paths are rejected. The collector checks samples through the same clock-bounds algorithm before accepting the complete clock dataset.

A loopback clock server does **not** authenticate who uses the local port or prove that a tunnel is trusted; tunnel policy, credentials and host provenance belong to the external operator. Do not expose the reference server directly on a public network. The reference and the experiments are not deployed automatically.
