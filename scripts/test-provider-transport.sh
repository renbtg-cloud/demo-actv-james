#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ROOT/build/test-provider-transport"
javac -encoding UTF-8 -cp "$ROOT/build/dist/runtime-rabbit.jar" -d "$ROOT/build/test-provider-transport" "$ROOT/tests/java/demo/ProviderHttpGatewayContract.java"
java -cp "$ROOT/build/test-provider-transport:$ROOT/build/dist/runtime-rabbit.jar" demo.ProviderHttpGatewayContract
