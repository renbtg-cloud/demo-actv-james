# Milestone 132 — Reproduce the original signed preflight before final merge

`simulator/readiness_handoff.py` reloads the saved collector verdict, the original exact host readiness files and all signed envelopes, validates pinned keys against the collector's manifest and fresh session, and recomputes the entire verdict using its recorded time/policy. It refuses any unequal field, missing component, unsafe verdict path or signature inconsistency. Post-run invocation does not misapply the current wall time to an expired preflight barrier.
