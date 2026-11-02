#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[[ -f "$ROOT/build/dist/core.jar" && -f "$ROOT/build/dist/adapter-httpjdk.jar" ]] || "$ROOT/scripts/build.sh"
mkdir -p "$ROOT/build/test-adapter"
CP="$ROOT/build/dist/core.jar:$ROOT/build/dist/adapter-httpjdk.jar"
javac -cp "$CP" -d "$ROOT/build/test-adapter" "$ROOT/tests/java/demo/HttpAdapterContract.java"
java -cp "$ROOT/build/test-adapter:$CP" demo.HttpAdapterContract
