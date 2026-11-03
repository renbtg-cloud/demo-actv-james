# Milestone 151 — Strict target URL validation

Both offline readiness checking and optional dialing use the same URL parser. Invalid ports, credentials, fragments, queries, non-root paths, non-HTTP(S) schemes and plaintext remote targets fail. No network operation occurs for an invalid URL.
