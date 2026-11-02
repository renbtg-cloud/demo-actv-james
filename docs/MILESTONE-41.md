# Milestone 41 — Real Java multi-coordinator negative and positive controls

The witness contract test launches **two separate Python coordinators with two genuine Java 21 producer JVMs each** against two independently listening HTTP mock services sharing one in-memory idempotency ledger. This is a controlled protocol/test-double check, *not real Spring Boot/DB/provider evidence*. It reconciles 64 actual HTTP requests, status counts, both backend participation and one gateway-clock rate. Deliberately forged witness counts, statuses and clock values fail the global audit. The test explicitly records `SAME_KERNEL_OBSERVED` because these processes are on the same Linux host.

Additional negative controls reject remote upstream addresses, malformed/missing body lengths, oversize bodies, unknown paths, overwhelmed witness admission and transport failure. Rejections are not misclassified as successful service admissions.

Command: `python3 -m unittest discover -s simulator/tests -p test_ingress_witness.py -v`.
