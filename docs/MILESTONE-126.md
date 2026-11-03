# Milestone 126 — Complete signed readiness without partial downgrade

`host_matrix.py` accepts optional `--host-session`, `--host-pins` and repeated `--signed-readiness` options. If any signed-readiness input is supplied, **all three components and a signed proof for every shard** must be present. Duplicate, missing, misassigned or in-memory substitute reports are rejected. Unauthenticated default preflight remains available and is explicitly marked `NOT_REQUESTED` in the result; it never silently pretends to be signed.
