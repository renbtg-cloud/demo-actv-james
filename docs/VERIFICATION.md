> **Historical milestone-01 verification.** Milestone-02 results and commands are in [Milestone 02](MILESTONE-02.md) and the root [README](../README.md).

# Evidence ledger

All tests below ran against Java classes compiled from **this integrated tree** and the laboratory's real PostgreSQL/RabbitMQ processes.

| Set | Complete? | Failures / total | Passed / total | Evidence |
|---|---|---:|---:|---|
| Integrated end-to-end final | yes | 0/53 | 53/53 | logs/integrated-release.log |
| Targeted old P20 contract checks | yes | 0/15 | 15/15 | logs/targeted-legacy-selfcontained.log |
| Old P20 full regression | **no — process command timeout** | not claimed | 75 passed before interruption | logs/p20-regression-integrated.log |

The 15 P20 assertions exclude 6 setup assertions from the older harness's separate counter. The regression timeout is not a regression failure, nor proof that the remainder passes. The laboratory's previous P20 414/414 suite is historical evidence for **earlier** source and must not be relabeled as a full regression of this integration.

The integrated test logs the schema and SQLite file used for each run. Test PostgreSQL schemas and broker queues are intentionally not automatically dropped; keep or clean up only the specific generated run IDs when no other tests are using them.
