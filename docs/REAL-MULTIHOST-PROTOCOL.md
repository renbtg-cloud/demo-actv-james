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
