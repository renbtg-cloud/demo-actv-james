# Milestone 27 — Prevent irrelevant third-party apt outages from failing CI

## Reproduced evidence

The real `oidc-keycloak` job in GitHub Actions run
[41495410203](https://github.com/renbtg-cloud/demo-actv-dryqe/actions/runs/41495410203)
failed with exit code **100** during `sudo apt-get update -qq` before Maven,
Keycloak or application acceptance was started. Its log stated:

`E: The repository 'https://packages.microsoft.com/ubuntu/26.04/prod resolute Release' no longer has a Release file.`

That is an external Ubuntu runner image bootstrap failure, not a passed or failed
Deroyque behavioral assertion. The rest of the matrix must still be assessed.

## Surgical correction

For each `*.list` and `*.sources` file under `/etc/apt/sources.list.d/`, only
when the file contains `packages.microsoft.com`, the disposable CI runner
renames it to `*.disabled`. This is intentionally scoped to a third-party
source not needed by this Java/PostgreSQL/Python project; no system-wide
repository errors are swallowed. Ubuntu repository `apt-get update` and the
`postgresql-client` installation remain mandatory, with up to three transient
fetch retries. Only the GitHub Actions runner changes, never user machines or
production hosts. The correction should be verified on a fresh runner by the
entire nine-job matrix rather than declared successful from local YAML syntax.

**Validation status:** Source correction is versioned; full GitHub Actions
verification required. No claim that a transient external repo failure proves
the code passed.
