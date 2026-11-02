# Milestone 39 — Bounded independent HTTP gateway witness

## Purpose

Distributed producer timestamps cannot simply be added across hosts, and per-JVM `System.nanoTime()` clocks cannot be combined as if shared. Introduce an **optional, separate loopback process** as an explicit instrumentation boundary. It forwards actual authorized HTTP requests round-robin to separately running service JVMs, while measuring each forwarded POST and its observed HTTP response through **one clock**.

`simulator/ingress_witness.py` is Python standard-library-only. It binds exclusively to `127.0.0.1`, accepts at most 16 unique loopback backend URLs, limits request and response sizes, and places a bounded semaphore ahead of backend forwarding. Incoming `Authorization`, `Content-Type`, and `X-Deroyque-Load-Run` are forwarded as necessary, but **never retained in results**. Unrecognized routes, oversize/ambiguous body framing and overload are rejected locally and counted separately. The witness records no credentials, payloads or full request logs. Do not expose it directly to a network; connect remote load machines by a correctly authenticated, encrypted tunnel with appropriately controlled access.

## What this does NOT mean

The gateway adds its own overhead and possibly becomes the bottleneck. Its monotonic window measures *gateway-visible HTTP response throughput*, **not provider effect throughput** and not backend-only saturation. This is laboratory instrumentation, not a hardened ingress gateway, TLS termination, production authentication boundary, hardware attestation or distributed tracing system.
