# Milestone 108 — Opt-in safe network readiness

By default preflight performs no network access. With `--check-network`, the
host makes only a TCP connection to loopback HTTP or a certificate-validated
TLS connection to an HTTPS base URL. No bearer token, HTTP request, private key
or application payload is transmitted. An open socket or valid TLS handshake
does not prove API authorization, cluster health or full network isolation.
Redirects and plaintext non-loopback URLs are not used.
