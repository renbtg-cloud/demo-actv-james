# Milestone 157 — One endpoint policy for planning and preflight

Both plan generation and producer-host preflight use the same URL parser, rejecting malformed ports, credentials, remote plaintext, query/fragment, and unsupported paths before publication. All numeric loopback IPs are consistently considered local. HTTPS still requires valid server certificates during opt-in probe.
