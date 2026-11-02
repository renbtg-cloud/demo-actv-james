# Milestone 33 — Archive type and executable-mode integrity

## What changed

The ZIP now records each Git-tracked regular file's canonical Git executable mode (`100644` or `100755`) alongside SHA-256. During ZIP verification it rejects symbolic links, directories, special file types, unexpected permission bits, and mode/content declarations that disagree. The independent CI checkout additionally compares the ZIP's executable modes to its own Git index, preventing a same-byte but different-permission artifact from passing. The source format is deliberately advanced from `deroyque-git-source-v1` to `-v2` so consumers cannot silently assume v1 means mode verification.

Adversarial regressions construct an executable script, flip a ZIP file's permissions after packaging, and forge a symlink entry while retaining the original payload. These tests operate on real Git indexes and ZIP metadata, rather than string mocks.

## Caveats

The ZIP verification *does not extract files* or establish a secure extraction policy in arbitrary third-party tools. It rejects nonregular members so that downstream tooling does not accidentally follow a symlink, but should not be used as a generic ZIP unpacker. Old v1 source artifacts are intentionally rejected by this v2 verifier; retain the old verifier if validating historical v1 snapshots. This is a source-provenance improvement, not a signed release or a trust guarantee for GitHub's infrastructure.
