# Milestone 46 — Fail-closed repeated-trial comparison

`simulator/distributed_merge.py` adds a deterministic SHA-256 signature of the complete workload contract, excluding the wall-clock UTC start barrier. `simulator/trial_compare.py` requires **at least three independently completed and reconciled runs**, each with distinct run ID, identical workload signature, matching backend count and host-provenance classification, positive gateway-clock rate, all attempted POSTs recorded, no diagnostic violations, and all unique requests verified as terminal `DONE`.

Only then does it report the descriptive median, minimum, maximum and median absolute deviation of observed gateway HTTP response rates. It intentionally does not present statistical confidence intervals with ungrounded sampling assumptions, establish causality, infer hardware equivalence, claim source-only saturation or convert admissions to independently completed provider effects.

Example after collecting 3 or more verified merged reports:

```sh
python3 simulator/trial_compare.py trial-01/merged.json trial-02/merged.json trial-03/merged.json --output comparative-gateway.json
```

The distinct trial runs and their raw evidence must be retained by the evaluator. The current CI only checks the comparator's positive/negative controls; it does not claim that a long multi-trial production-capacity campaign has been executed.
