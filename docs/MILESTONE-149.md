# Milestone 149 — Atomic owner-only readiness publication

The Linux producer CLI now reuses the collector’s fsynced, owner-only, atomic no-replace publish mechanism. A test invokes the actual CLI with umask 000 and verifies mode 0600 and complete JSON; no world-readable creation window.
