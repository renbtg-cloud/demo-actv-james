#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
[[ -f "$ROOT/build/dist/core.jar" ]] || "$ROOT/scripts/build.sh"
mkdir -p "$ROOT/build/test-http-facade"
javac -encoding UTF-8 -cp "$ROOT/build/dist/core.jar" -d "$ROOT/build/test-http-facade" "$ROOT/tests/java/demo/AdmissionHttpFacadeContract.java"
java -cp "$ROOT/build/dist/core.jar:$ROOT/build/test-http-facade" demo.AdmissionHttpFacadeContract
