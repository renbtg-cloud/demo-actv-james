# Milestone 85 — Fresh unpredictable run challenge and downgrade refusal

The independent collector creates a cryptographically random 256-bit hexadecimal
challenge for each experiment; signers bind its complete session digest into
their signed messages. A previously signed report from another challenge fails
verification even when run ID, workload and host public keys are unchanged.
Missing session-bound fields, downgrade to legacy report shape, and malformed
challenge encodings are rejected when session mode is requested.

The collector is responsible for truly fresh challenges and secure custody.
