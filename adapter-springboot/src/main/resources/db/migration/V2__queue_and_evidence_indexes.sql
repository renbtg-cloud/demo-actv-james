-- PostgreSQL-specific optimizations; do not impose these on HSQLDB/SQLite stores.
CREATE INDEX p9_outbox_due_pending_idx ON p9_outbox (due_ms, nid)
  WHERE published_ms IS NULL;
CREATE INDEX p9_request_due_idx ON p9_requests (stage, due_ms, audit_ms);
CREATE INDEX p9_observation_dispatch_idx ON p9_observation (did);
CREATE INDEX p9_dispatch_request_idx ON p9_dispatch (rid);
