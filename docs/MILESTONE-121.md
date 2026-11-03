# Milestone 121 — Crash-resistant exclusive, owner-only verdict publication

The matrix CLI writes an owner-only 0600 temporary result, fsyncs, atomically links it to the desired path without replacement, syncs the directory and removes the temporary link. Existing results and symlinks cannot be overwritten. This is a POSIX/Linux collector operation, not a distributed transaction.

Verification: `python3 -m unittest discover -s simulator/tests -p "test_host_matrix*.py" -v`. The real multi-host host-provenance acceptance remains pending independently provisioned machines.
