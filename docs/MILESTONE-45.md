# Milestone 45 — Bounded, auditable gateway round-trip latency histogram

A fixed-bin histogram now records duration between gateway forwarding start and fully read upstream response, inclusive of network and proxy overhead. No per-request identities, bodies or Authorization headers are retained. Buckets are fixed `<1`, `<5`, `<10`, `<25`, `<50`, `<100`, `<250`, `<500`, `<1000`, `<5000`, `<20000` milliseconds, and `>=20000` ms. Histogram counters must sum exactly to the number of finished forwarded POSTs; unknown, negative or boolean counters fail the global audit.

The histogram does **not** measure end-user wall time, signed-provider effect latency, pure Spring processing time, queueing after the response, GC roots or distributed trace spans. It is intended as bounded laboratory evidence to compare trial conditions, never as a stand-alone performance certification.
