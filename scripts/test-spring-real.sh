#!/usr/bin/env bash
# Actual Spring Boot test only: never substitutes JDK HTTP or stand-in APIs.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
command -v mvn >/dev/null 2>&1 || { echo 'BLOCKED: Maven is required for a real Spring Boot build.' >&2; exit 3; }
command -v java >/dev/null 2>&1 || { echo 'BLOCKED: Java is required.' >&2; exit 3; }
mvn -B -ntp -f "$ROOT/adapter-springboot/pom.xml" clean package
exec python3 -u "$ROOT/tests/run_spring_live_acceptance.py" --mode boot \
  --boot-jar "$ROOT/adapter-springboot/target/deroyque-springboot-0.10.0-SNAPSHOT.jar" "$@"
