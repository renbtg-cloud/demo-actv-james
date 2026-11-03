# Milestone 158 — Bounded heap, concurrency and UTC launch inputs

The generator enforces 32..65536 MiB Java heap maxima, concurrency 1..10000, launch delay 30..86400 seconds, and a valid integer reference UTC time. These are input guards, not estimates that the operator host can sustain the chosen resource budget.
