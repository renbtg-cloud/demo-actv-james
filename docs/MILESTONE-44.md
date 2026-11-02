# Milestone 44 — Downstream delivery failures are separate evidence

The gateway's single-clock upstream-response measurement is made **before** the gateway finishes writing the response body to the client. A successful backend response is therefore not necessarily a successfully delivered client response, much less a committed provider effect.

The witness now counts attempted downstream deliveries that failed at the socket write boundary in `client_delivery_failures` (without logging payloads or credentials). The global auditor fails closed whenever this counter is nonzero. This is best-effort socket-level reporting and cannot attest to the remote client application's actual consumption of bytes. The earliest loss may also surface independently as a producer transport failure.

The regression includes an upstream 202 followed by a simulated delivery failure, plus rejected ambiguous Content-Length and chunked request framing. The latter are not used as proof of production-grade HTTP request-smuggling immunity; this is a bounded loopback laboratory tool.
