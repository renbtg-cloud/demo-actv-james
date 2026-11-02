# Milestone 29 — Independent Python/Java JSON differential contract

## Why

The Milestone 28 randomized Java test deliberately controls its own escaping;
that could allow the encoder and parser to agree on a shared mistake.
This milestone compares the actual compiled `StrictJson` Java implementation
in its own process against **Python 3.12 standard-library `json`**, rather than
another serialization routine in the same Java codebase.

## Experiment

`StrictJsonOracle` reads only synthetic UTF-8 JSON test lines from stdin and
outputs a status plus base64 of the parsed `externalId`. A Python generator
uses fixed seed `20261102`, 1,200 valid cases mixing literal and escaped
Unicode scalars (including supplementary-plane characters, quotes and control
escapes), 100 JSON objects with duplicate keys disguised by `\u` escapes,
100 unpaired-surrogate cases, and 100 truncated JSON objects.

Python enforces the **specific agreed application contract** on top of
stdlib parsing: exactly the three string-valued fields, no duplicate decoded
object keys and no unpaired UTF-16 surrogate code units. Those extra rules are
not claimed as Python's default JSON behavior; this is why the oracle uses an
`object_pairs_hook` and explicit Unicode-scalar verification.

A discrepancy causes a nonzero process exit and fails the CI job, with indices
rather than the synthetic payload text printed. The local real Java/Python
comparison reported **0/1500 failures, 1500/1500 passed**, and exit 0.

## Boundaries

This compares an intentionally restricted JSON input schema, not complete JSON
parsers, RFC compliance for every media type, Unicode normalization, or real
HTTP framing. The full Spring JWT/DB/provider acceptance suite runs separately
in the same CI job. The seed and vector count are fixed for reproducibility,
not a proof against all possible payloads.
