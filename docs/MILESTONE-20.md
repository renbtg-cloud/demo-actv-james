# Milestone 20 — Optional local Keycloak OIDC distribution

## Scope and motivation

Issue [#1](https://github.com/renbtg-cloud/demo-actv-dryqe/issues/1) tracks the
option to evaluate Deroyque with a **locally supplied Keycloak** rather than
requiring an existing organizational Keycloak. This option must never be required
for users configuring their own OIDC-compatible issuer. All business handlers and
JWT authorization decisions remain unchanged.

## User workflow

- `python3 scripts/local_keycloak.py up` creates a local disposable Keycloak
  26.8.1 container, on `127.0.0.1:18090` by default, with a built-in realm.
- `python3 scripts/local_keycloak.py status` queries real OIDC discovery to ensure
  the issuer is available and consistent.
- `eval "$(python3 scripts/local_keycloak.py env --provider local)"` emits only
  public configuration: issuer, JWKS, audience, an explicit loopback HTTP test
  exception, and `APP_AUTH_MODE=jwt`. It emits **no client secrets**.
- `python3 scripts/local_keycloak.py token --client reader|writer|outsider` gets
  a real short-lived signed Keycloak access token using the appropriate generated
  service account, then prints it **only when explicitly requested**. Treat output
  as a secret and do not put it in logs.
- `python3 scripts/local_keycloak.py down` removes the known, labeled Docker
  container and generated state/credentials.
- For an externally managed provider use `env --provider external --issuer ...
  --jwks-uri ... --audience ...`. External issuer/JWKS endpoints must be HTTPS;
  the loopback override is explicitly unset.

The script requires Python 3.12+ and Docker. It does **not** start PostgreSQL,
RabbitMQ, the independent activation provider or Deroyque itself. Start those
through the existing development/CI machinery. Realm import clients are
`deroyque-reader`, `deroyque-writer`, `deroyque-outsider`, and a wrong-audience
negative-test client. The main realm is `deroyque-local`, with the audience
`deroyque-activation-api`; a second `deroyque-local-untrusted` realm is created
only when `--with-test-realm` is explicitly selected for acceptance testing.

## Safety properties and limitations

- **No Keycloak auto-start**: choosing an external provider requires no Docker.
- `start-dev` and HTTP are **development/test only**. No claim of hardened TLS,
  production user management, persistence, HA, or automatic signing-key rotation.
- Host-facing Docker binding is `127.0.0.1` only; `APP_JWT_ALLOW_HTTP_LOOPBACK`
  never allows non-loopback HTTP. Container removal requires the locally generated
  name and ownership label. No existing Docker instances are managed.
- Generated client secrets and admin bootstrap password are random for each run.
  State and credentials are stored under `.local/keycloak` with private file
  permissions. Realm JSON files are readable by the non-root Keycloak process
  inside its bind mount; the host's enclosing state directory is private.
- Users handling bearer tokens must not log, publish, or commit those credentials.
  The `token` command prints the token only at explicit request.
- The local Keycloak option does not weaken the Spring JWT fail-closed default;
  it configures that same resource server rather than choosing `APP_AUTH_MODE=lab`.

## Verification

`python3 -m unittest discover -s tests -p test_local_keycloak.py -v` checks the
realm/client structure, HTTPS-only external configuration, process isolation,
private state, loopback Docker port mapping, and cleanup without running Docker.

A distinct `local-keycloak` GitHub Actions matrix job executes those unit checks,
starts the **first-class optional local provider** using `up --with-test-realm`,
then runs the existing genuine Spring/Keycloak/SQLite/PostgreSQL/RabbitMQ
end-to-end acceptance harness through `--use-local-keycloak`. The original
`oidc-keycloak` job is retained as an independent control. This checks the
actual packaged commands instead of merely proving that Keycloak itself works.

**Verified outcome (2 November 2026):** the actual [GitHub Actions run
41475178125](https://github.com/renbtg-cloud/demo-actv-dryqe/actions/runs/41475178125)
finished `success`. The new `local-keycloak` job passed **0/26 failures;
26/26 integration assertions passed**, after **0/4 failures; 4/4 local
configuration unit checks passed**. All eight previously existing jobs also
passed: **0/264 failures; 264/264 integration assertions passed** across all
nine jobs. The load regression exercised four Spring Boot JVMs, two SIGKILLs,
24 genuinely overlapping producer JVMs, 960 HTTP POSTs and 160 independently
persisted effects for 160 unique identities.

This is a real Docker/Keycloak/Spring/JDBC/RabbitMQ/SQLite CI integration result,
not a production deployment or operational security certification.

