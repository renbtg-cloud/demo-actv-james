#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
./scripts/build.sh
./scripts/test-adapter.sh
export DEROYQUE_TEST_CP="$(./scripts/print-classpath.sh)"
python3 -u tests/run_end_to_end.py
python3 -u tests/legacy/run_targeted.py
