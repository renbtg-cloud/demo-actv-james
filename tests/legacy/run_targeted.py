#!/usr/bin/env python3
"""Selected contract checks against the SAME compiled classes as the integrated app.
 No separate recompilation. Uses disposable schemas and independent provider SQLite.
"""
import run_p20 as p
try:
 p.case_terminal_contradiction()
 p.case_signed_issuer_lies()
 p.case_misconfigured_status_contract()
 print(f'FINAL TARGETED: FAILED {p.COUNTS["failed"]}/{sum(p.COUNTS.values())}; PASSED {p.COUNTS["passed"]}/{sum(p.COUNTS.values())}',flush=True)
except BaseException as e:
 print('FINAL TARGETED ERROR',repr(e),'COUNTS',p.COUNTS,flush=True)
 raise
