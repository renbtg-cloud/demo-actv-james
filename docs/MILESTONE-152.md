# Milestone 152 — Plaintext local alias resolution pinned to numeric loopback

HTTP probes reject localhost aliases returning any non-loopback candidate and connect to the selected numeric loopback address, avoiding a second name resolution. This cannot authenticate an HTTP application on loopback or establish a secure external tunnel.
