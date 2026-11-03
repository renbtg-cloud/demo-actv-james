# Milestone 119 — Pinned, bounded, nonblocking readiness file intake

Host-readiness files are opened with O_NOFOLLOW and O_NONBLOCK, checked as regular files, and capped at 131072 bytes using the opened descriptor. Final symlinks, FIFOs, oversized inputs and files changed in size during the read are rejected. Parent-directory custody remains the operator responsibility.

Verification: `python3 -m unittest discover -s simulator/tests -p "test_host_matrix*.py" -v`. The real multi-host host-provenance acceptance remains pending independently provisioned machines.
