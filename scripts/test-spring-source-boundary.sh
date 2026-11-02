#!/usr/bin/env bash
# Compile/run against *test-only stand-ins*. NOT a real Spring Boot integration test.
set -euo pipefail
R="$(cd "$(dirname "$0")/.." && pwd)"
[[ -f "$R/build/dist/runtime-rabbit.jar" ]] || "$R/scripts/build.sh"
D="$R/build/spring-standins"
rm -rf "$D";mkdir -p "$D"
mapfile -t stubs < <(find "$R/tests/spring-api-stubs" -name '*.java')
CP="$($R/scripts/print-classpath.sh)"
javac -encoding UTF-8 -cp "$CP" -d "$D" "${stubs[@]}"   "$R/adapter-springboot/src/main/java/demo/SpringDeroyqueApplication.java"   "$R/adapter-springboot/src/main/java/demo/SpringRequestController.java"   "$R/tests/java/demo/SpringSourceBoundaryTest.java"
java -cp "$D:$CP" demo.SpringSourceBoundaryTest
