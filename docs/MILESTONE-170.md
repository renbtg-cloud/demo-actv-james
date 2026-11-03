# Milestone 170 — Independent CLI and CI acceptance gate

A real separate Python process must reject a modified manifest before writing any readiness evidence. The Linux-host-readiness GitHub Actions job runs the contract tests explicitly; real hosted CI acceptance is gated on the workflow result, not local claims.

Scope: offline software-level consistency on an operator-controlled trusted plan, not authenticated physical hosts, not a production benchmark, and not proof of universal exactly-once effects.
