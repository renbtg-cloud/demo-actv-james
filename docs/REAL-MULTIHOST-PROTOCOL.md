# Real independent-host experiment — operator protocol

**Status:** executable preparation and offline verification are implemented. No
claim of physical independent-host execution is made until the evaluator has
actually run on separately administered hosts and preserved the independent
results. Running two processes, containers, or JVMs on one kernel is explicitly
not enough. Work only against an application and infrastructure you are
authorized to stress; avoid production accounts and real customer data.

## Trust model and prerequisites

- Have **at least two independently provisioned Linux hosts** with JDK 21+
  (`java` and `javac`), Python 3.12+, adequate CPU/RAM/disk, shared HTTPS API
  access (or authenticated SSH tunnels to local loopback), and UTC clock
  synchronization. Independent administrative and hardware failure domains
  should be established separately by the operator.
- The trusted collector controls the launch manifest, a fresh per-run signing
  challenge, the pinned public keys, the exact simulator source ZIP and final
  verification. The producer hosts retain their own **private** Ed25519 keys.
- Bearer credentials remain private environment variables on individual hosts.
  **Never** copy tokens, private keys, raw credential files, or unrelated
  provider/database dumps into transported evidence or GitHub artifacts.
- The incoming ZIP format and collector preserve source integrity. Public-key
  signatures identify pinned keys, **not independently attested physical
  computers**. The kernel fingerprint is run-salted and self-reported.

## 1. Prepare a common launch plan at the trusted collector

Use an *existing authorized* HTTPS API endpoint and choose a barrier far enough
in the future to ship the plan, check the hosts and initialize the key/session.
The example uses placeholders; replace them before executing.

```sh
python3 simulator/distributed_prepare.py \
  --url 'https://AUTHORIZED-API.example' \
  --shards 2 --jvms-per-host 2 --requests 480 --keys 120 \
  --profile steady --rate 4 --run-id evidence-run-001 \
  --start-after-seconds 900 \
  --output-dir .local/evidence-run-001
```

The resulting directory contains `manifest.json` and `launch-host-000.sh` /
`launch-host-001.sh`. The plan fixes the exact workload and the start barrier.
If the barrier expires during preparation, **create a fresh run plan and
signing session**; do not edit the timestamp in either file.

## 2. Generate and pin host signing keys

On each host, in a private directory:

```sh
umask 077
openssl genpkey -algorithm Ed25519 -out host-private.pem
openssl pkey -in host-private.pem -pubout -out host-public.pem
chmod 600 host-private.pem
```

Transfer only the public keys over an authenticated channel and independently
check which key belongs to which intended host. At the collector, create
`pins.json` as a JSON object mapping `host-0` and `host-1` to the exact PEM
public-key strings (including the newline escapes). Do **not** include any
private key in `pins.json`.

Then at the collector:

```sh
python3 simulator/host_session.py \
  --manifest .local/evidence-run-001/manifest.json \
  --pins .local/pins.json \
  --hosts host-0,host-1 \
  --output .local/evidence-run-001/session.json
```

Distribute the exact manifest, session, public-key inventory and corresponding
launch script to each assigned host through your authenticated administrative
channel. Preserve the exact source package and commit on both hosts.

## 3. Preflight on each actual producer host

From the repository root on host `i` (set `i=0` or `i=1` as appropriate):

```sh
python3 simulator/host_readiness.py \
  --manifest .local/evidence-run-001/manifest.json \
  --shard-index 0 \
  --launcher .local/evidence-run-001/launch-host-000.sh \
  --check-network \
  --output .local/evidence-run-001/host-000-readiness.json
```

This example is for host 0; host 1 must use shard index 1, launcher 001 and a
unique report path. `--check-network` tests a TCP connection (or CA-verified
TLS handshake), **not** application authorization; omit it if a network probe
would violate lab policy and keep the `NOT_PROBED` classification honest. A
host may legitimately reject a plan if the barrier has fewer than 30 seconds
remaining or local resources are insufficient. Correct the prerequisites and
regenerate the plan/session instead of disabling validation.

## 3a. Optional session-bound *signed readiness* (M123–M130)

For a collector-challenged preflight, on **each actual producer host** install
`cryptography>=42,<50` and sign the unmodified readiness JSON immediately
after preflight. Use the *host's own private key*, previously enrolled at the
collector; retain it locally with owner-only permissions:

```sh
python3 simulator/host_readiness_signature.py \
  --report .local/evidence-run-001/host-000-readiness.json \
  --private-key .local/host-private.pem --host-id host-0 \
  --session .local/evidence-run-001/session.json \
  --manifest .local/evidence-run-001/manifest.json \
  --pins .local/pins.json \
  --output .local/evidence-run-001/host-000-readiness-proof.json
```

Host 1 uses its own host ID `host-1`, key, report and distinct output file.
Transfer each **report and detached signed envelope** using an authenticated
operator-controlled channel. The signature commits to the exact report file
bytes. Keep all private keys on their original producer hosts.

At the **trusted collector**, use the collector-held manifest, pinned public
keys and challenge session, **not copies supplied within untrusted uploads**.
Run the matrix with every host proof, in any arrival order:

```sh
python3 simulator/host_matrix.py \
  --manifest .local/evidence-run-001/manifest.json \
  --host-pins .local/pins.json \
  --host-session .local/evidence-run-001/session.json \
  --signed-readiness .local/evidence-run-001/host-000-readiness-proof.json \
  --signed-readiness .local/evidence-run-001/host-001-readiness-proof.json \
  --strict-temporal --require-network \
  --output .local/evidence-run-001/signed-readiness-matrix.json \
  .local/evidence-run-001/host-000-readiness.json \
  .local/evidence-run-001/host-001-readiness.json
```

`--strict-temporal` is **only for near-live preflight** (not later archival
rechecks). `--require-network` is only appropriate if both producers actually
completed an authorized network probe. A complete signed proof set is mandatory
when signing is requested; there is no partial-signature fallback. The result
reports only `ALL_PINNED_KEYS_VERIFIED_NOT_HOST_ATTESTATION`: even correct
signatures cannot attest physical machine ownership, truthful CPU/kernel
reports, synchronized clocks or end-to-end activation semantics.

The unsigned compatibility workflow remains available below; its verdict says
`NOT_REQUESTED` for signed readiness and must never be reported as signed.
See `docs/MILESTONE-123.md` through `docs/MILESTONE-130.md`.

Bring the two readiness reports back to the trusted collector. Check the
self-reported kernel-fingerprint collision gate:

```sh
python3 simulator/host_matrix.py \
  --manifest .local/evidence-run-001/manifest.json \
  --require-network \
  --output .local/evidence-run-001/readiness-matrix.json \
  .local/evidence-run-001/host-000-readiness.json \
  .local/evidence-run-001/host-001-readiness.json
```

For stricter, time-sensitive *preflight* collection, add `--strict-temporal`
with optional `--max-age-ms 300000 --max-future-ms 10000` to the matrix command.
These checks compare self-reported readiness timestamps against the collector's
own clock, require at least 30 seconds remaining before the launch barrier and
verify the embedded barrier matches the manifest top-level barrier. Failed
preflights refuse verdict publication. **Do not use this option for late
post-run archival verification**: it deliberately rejects expired barriers.
The output now includes canonical SHA-256 commitments for each report and is
published as an owner-only, immutable result; already existing files are not
replaced. This is local evidence integrity, not remote host authentication.
See `docs/MILESTONE-116.md` through `docs/MILESTONE-122.md`.

Only use `--require-network` when both hosts actually performed the probe. A
successful matrix reports `APPARENT_READY_DISTINCT_KERNEL_REPORTS_UNVERIFIED`.
Matching fingerprint values reject the independent-host claim; distinct
self-reports do **not** authenticate physical independence. Use separately
recorded infrastructure/administrative evidence for that claim.

If comparable cross-host timing matters, use `simulator/clock_probe.py` with
an authenticated SSH tunnel to the same trusted UTC reference, then attach
complete clock reports to signed host envelopes. Unsigned wall-clock values in
readiness reports are not sufficient for global throughput inference.

### Preserve signed preflight for later independent re-verification (M131–M138)

The collector must retain the unmodified `signed-readiness-matrix.json`, exact
preflight readiness JSONs, detached per-host Ed25519 readiness proofs, its own
manifest, session, and host pin enrollment. The matrix preserves its declared
collector-check time and policy budgets, so a post-run verifier can reproduce
its *historical claim* even after the launch barrier has elapsed.

At the end of step 7, additionally supply the saved preflight documents:

```sh
python3 simulator/distributed_merge.py \
  --launch-manifest .local/evidence-run-001/manifest.json \
  --host-pins .local/pins.json \
  --host-session .local/evidence-run-001/session.json \
  --signed-host-report .local/evidence-run-001/host-000-signed.json \
  --signed-host-report .local/evidence-run-001/host-001-signed.json \
  --readiness-matrix .local/evidence-run-001/signed-readiness-matrix.json \
  --readiness-report .local/evidence-run-001/host-000-readiness.json \
  --readiness-report .local/evidence-run-001/host-001-readiness.json \
  --signed-readiness .local/evidence-run-001/host-000-readiness-proof.json \
  --signed-readiness .local/evidence-run-001/host-001-readiness-proof.json \
  --require-distinct-kernels \
  --output .local/evidence-run-001/two-phase-final-verdict.json \
  simulator/results/evidence-run-001-host-000 \
  simulator/results/evidence-run-001-host-001
```

For the offline ZIP importer, pass the **corresponding independently verified
shard folders** instead of the original `simulator/results/...` paths. The
post-run summaries must preserve the same run-salted `kernel_fingerprint` as
preflight, and the signed host reports must refer to those exact shard bytes.
This command does **not** include separate `--verify-state` credentials or
external provider/database evidence; the full acceptance process must still
perform those checks as described in step 7. Signing and checking locally
reported data cannot authenticate when a remote host really ran, or what its
hardware, clock or administrative control actually was.

### Optional independently pinned collector checkpoint (M139–M146)

After the signed matrix has passed **and before** the launch barrier, an
operator may pin a separate collector signing key. Prefer an evaluator-generated
Ed25519 key, kept owner-only and distinct from every producer key. Obtain its
public key over an authenticated, independent channel. The public key must NOT
come from the untrusted readiness or receipt upload.

```sh
umask 077
openssl genpkey -algorithm Ed25519 -out .local/collector-private.pem
openssl pkey -in .local/collector-private.pem -pubout \
  -out .local/collector-public.pem
chmod 600 .local/collector-private.pem

python3 simulator/readiness_checkpoint.py sign \
  --matrix .local/evidence-run-001/signed-readiness-matrix.json \
  --report .local/evidence-run-001/host-000-readiness.json \
  --report .local/evidence-run-001/host-001-readiness.json \
  --signed-readiness .local/evidence-run-001/host-000-readiness-proof.json \
  --signed-readiness .local/evidence-run-001/host-001-readiness-proof.json \
  --session .local/evidence-run-001/session.json \
  --manifest .local/evidence-run-001/manifest.json \
  --pins .local/pins.json \
  --collector-key .local/collector-private.pem \
  --receipt .local/evidence-run-001/preflight-checkpoint.json
```

An independently enrolled evaluator verifies the receipt with exactly the same
arguments except `verify` instead of `sign` and
`--collector-key .local/collector-public.pem`. The verifier still checks the
original signed readiness reports. The checkpoint signs what the collector
*claims* it observed; the timestamp is not anchored in a trusted timestamping
service and can be backdated by a dishonest collector.

To make this verification **mandatory** in the final merge example above, add:

```sh
  --preflight-checkpoint .local/evidence-run-001/preflight-checkpoint.json \
  --collector-public-key .local/collector-public.pem
```

Add these options before `--output` in the actual `distributed_merge.py`
command. If either option is missing or invalid, the global verdict fails.
This is an opt-in additional integrity gate, not a claim of real remote
multi-host acceptance, verified CPU placement or exactly-once provider effects.

## 4. Run actual JVM producers at the shared start barrier

On each host, set `DEROYQUE_SIM_WRITE_TOKEN` **locally**, with appropriate
scopes. Never put it in a shell script, manifest, JSON report or command-line
argument. Invoke the corresponding launch script **from the repository root**.
The generated script builds the Java simulator and starts the assigned JVMs:

```sh
sh .local/evidence-run-001/launch-host-000.sh
```

Host 1 runs `launch-host-001.sh`. Capture exit codes locally. Allow the
application and independent provider to reach terminal states; preserve
accurate run, fault and clock observations. If a host or network fails, retain
that evidence rather than retrospectively replacing a failed run with a pass.

## 5. Sign and transfer the exact completed shards

On each producer host, with the appropriate host ID and private key:

```sh
python3 simulator/host_signed_evidence.py sign \
  --folder simulator/results/evidence-run-001-host-000 \
  --run-id evidence-run-001 --host-id host-0 --shard 0 \
  --private-key .local/host-private.pem \
  --session .local/evidence-run-001/session.json \
  --manifest .local/evidence-run-001/manifest.json \
  --pins .local/pins.json \
  --output .local/evidence-run-001/host-000-signed.json

python3 simulator/shard_bundle.py create \
  --folder simulator/results/evidence-run-001-host-000 \
  --signed-envelope .local/evidence-run-001/host-000-signed.json \
  --output .local/evidence-run-001/host-000.zip
```

Repeat with host 1's **own** private key, shard 1, host ID `host-1`, its own
report and result directory. The example omits signed clock reports; if clock
evidence is required, supply `--clock-report` when signing and bundling so
all hosts use a complete matched evidence set. Transfer only the bounded
`.zip` files through an authenticated administrative channel.

## 6. Independently collect and verify at the trusted collector

```sh
python3 simulator/shard_collection.py \
  --pins .local/pins.json \
  --session .local/evidence-run-001/session.json \
  --manifest .local/evidence-run-001/manifest.json \
  --require-distinct-kernels \
  --output-dir .local/evidence-run-001/verified-import \
  .local/evidence-run-001/host-000.zip \
  .local/evidence-run-001/host-001.zip

python3 simulator/verify_collection.py \
  --collection .local/evidence-run-001/verified-import \
  --pins .local/pins.json \
  --session .local/evidence-run-001/session.json \
  --manifest .local/evidence-run-001/manifest.json \
  --original-bundles .local/evidence-run-001/host-000.zip \
                     .local/evidence-run-001/host-001.zip
```

If signed import fails, retain the rejected originals for diagnosis. Do not
manually modify a copied record to make it pass. The collector uses private
staging, verifies the trusted session and pinned signatures, and refuses to
overwrite a previously published collection.

## 7. Audit the global workload after import

```sh
python3 simulator/distributed_merge.py \
  --launch-manifest .local/evidence-run-001/manifest.json \
  --host-pins .local/pins.json \
  --host-session .local/evidence-run-001/session.json \
  --require-host-session --require-host-signatures \
  --require-distinct-kernels \
  --signed-host-report .local/evidence-run-001/verified-import/shard-000/signed-envelope.json \
  --signed-host-report .local/evidence-run-001/verified-import/shard-001/signed-envelope.json \
  --output .local/evidence-run-001/global-verdict.json \
  .local/evidence-run-001/verified-import/shard-000 \
  .local/evidence-run-001/verified-import/shard-001
```

A `PASS` result requires verified ingress outcomes under the declared
experiment contract; it is not a universal exactly-once guarantee. For live
state verification, the collector needs a separately authorized read token,
with `--verify-state` or separately captured trusted DB/provider evidence.
To compare cross-host throughput, use independently bounded clock offsets,
server metrics from every active Spring JVM and a shared response-time witness;
do not add host-reported QPS numerically without common timing evidence.

## Acceptance report and known limits

Record the exact Git commit and canonical source ZIP hash, run identity,
complete launch plan, host IDs and their independently validated cloud/hardware
administrative placement, resource assignments, actual clock measurements,
per-host outcomes, independent provider and PostgreSQL effects, fault injection
and recovery intervals, terminal completion time bounds, signed envelopes,
collection audit and any failures. Explain which claims were tested on actual
separate hosts versus one kernel. Keep raw secrets and private keys out of
public evidence.

**Fail closed:** if readiness, signing, transport hashes, original ZIP
reverification, signed reconciliation or database/provider consistency disagree,
report the mismatch as a failed or inconclusive run, not as a successful result.
