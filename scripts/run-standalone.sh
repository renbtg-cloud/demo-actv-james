#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec java -cp "$("$ROOT/scripts/print-classpath.sh")" demo.DeroyqueApplication
