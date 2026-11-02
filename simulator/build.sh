#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p build/classes
javac --release 21 -d build/classes src/main/java/demo/simulator/LoadAgent.java
jar --create --file build/deroyque-input-simulator.jar -C build/classes .
echo 'SIMULATOR_BUILT build/deroyque-input-simulator.jar'
