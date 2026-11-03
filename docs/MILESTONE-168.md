# Milestone 168 — Historical v2 numeric rendering compatibility

The v2 manifest recorded normalized float values while original launcher text rendered integer defaults differently from explicit CLI float flags. The verifier enumerates only bounded canonical numeric renderings and requires the exact regenerated manifest to match, preserving previously published scripts.

Scope: offline software-level consistency on an operator-controlled trusted plan, not authenticated physical hosts, not a production benchmark, and not proof of universal exactly-once effects.
