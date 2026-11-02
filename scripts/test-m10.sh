#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)";cd "$ROOT"
./scripts/build.sh
./scripts/test-adapter.sh
./scripts/test-http-facade.sh
./scripts/test-spring-source-boundary.sh  # **NOT** real Spring Boot
python3 -m unittest discover -s simulator/tests -v
if [[ "${DEROYQUE_RUN_LIVE_TESTS:-0}" == "1" ]];then
  python3 -u tests/test_headless_engine.py
  export DEROYQUE_TEST_CP="$(./scripts/print-classpath.sh)"
  python3 -u tests/run_end_to_end.py
fi
