# Source provenance

- `service/src/main/java/demo/P13Service.java`, `JdbcRabbitLedger.java`, `P15Policy.java`, `P20SignedProof.java`, related test utilities, provider and test signing key fixtures originate from `DEROYQUE-P20-SIGNED-EVIDENCE-CONTRADICTIONS.zip` (source snapshot 2026-11-02).
- `PublicRequests.java`, `RestApi.java`, `StrictJson.java`, `DeroyqueApplication.java`, changed DDL, new /api/v1 endpoints, `tests/run_end_to_end.py`, and scripts/docs belong to this integrated milestone.
- The public admission in `PublicRequests` adds `customer_id` and `product_code` columns directly to the initial schema DDL; it does not claim to migrate prior schemas.
- The earlier `demoactv-full-20261031-gemini-audit-05.zip` was reviewed but used as design context only: its archive contained Markdown and no Java/Spring Boot application source.

Internal P-prefixed Java class names are technical debt, not separate active POC projects.
