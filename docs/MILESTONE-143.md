# Milestone 143 — Optional full-chain checkpoint gate in global merger

`distributed_merge.py` accepts paired `--preflight-checkpoint` and `--collector-public-key`. Specifying either requires both, a complete verified signed readiness handoff and every underlying signed post-run input. The final result records the checkpoint verification status under `readiness_handoff.collector_checkpoint`. Missing, incorrect or mismatched evidence forces overall `FAIL`, not an unsigned fallback. Default legacy behavior remains explicitly opt-in and does not claim a checkpoint.
