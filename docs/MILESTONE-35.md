# Milestone 35 — Bounded verification of hostile ZIP structures

## What changed

The verifier now inspects the ZIP central directory *before* decompressing any payload. It enforces a bounded number of members, maximum compressed/uncompressed sizes, per-member expansion ratio and metadata size, strictly rejects duplicate/unsafe names and unexpected ZIP compression, encryption or file types. Hash and CRC checking then read each approved regular member in fixed-size chunks rather than creating all decompressed file contents in memory.

Limits in the current laboratory implementation: 20,000 source members; 64 MiB uncompressed per source file; 512 MiB total expanded contents and compressed ZIP bytes; 500:1 maximum compression expansion ratio for members larger than 8 KiB; and 8 MiB per manifest file. Because the source project is small, exceeding a limit is treated as a refusal requiring explicit engineering review, not an automatic increase. Tests construct a highly compressible ZIP payload and verify it is rejected during metadata preflight; they also simulate a reduced maximum member size and test a forged symlink.

## Caveats

These are application-enforced resource limits, not protection against compromised decompression libraries or crafted attacks on the host process, nor a universal ZIP bomb detector. The tool does not extract untrusted ZIP contents. A crafted ZIP with lying central-directory data still receives a bounded stream-read and the `zipfile` parser's CRC/error checks; for untrusted arbitrarily large files, operate under OS memory/time limits. The source archive is not a general artifact store, and limits intentionally restrict unusually large repositories.
