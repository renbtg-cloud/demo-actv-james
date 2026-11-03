# Milestone 167 — Offline and host checks share the same contract

Independent offline verification and host-specific preflight share replay semantics. The offline verifier uses the bounded pinned-descriptor regular-file snapshot and does not block on FIFOs or follow symlinked launchers.

Scope: offline software-level consistency on an operator-controlled trusted plan, not authenticated physical hosts, not a production benchmark, and not proof of universal exactly-once effects.
