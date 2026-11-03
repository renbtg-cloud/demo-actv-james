# Milestone 153 — No accidental probe on failed validation

A hostile manifest target is rejected before invoking the network dialer; tests check that no connection is attempted on an invalid target. Remote HTTPS still uses standard certificate verification, not API authorization.
