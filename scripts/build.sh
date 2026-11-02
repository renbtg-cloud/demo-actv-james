#!/usr/bin/env bash
# Compile genuine separately classpathed Java artifacts; no Maven installation required in this offline lab.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PGJAR="${POSTGRES_JAR:-/mnt/data/postgresql-42.7.14.jar}"
AMQPJAR="${RABBITMQ_CLIENT_JAR:-/mnt/data/amqp-client-5.37.0.jar}"
[[ -f "$PGJAR" && -f "$AMQPJAR" ]] || { echo 'Missing JDBC/AMQP jars: set POSTGRES_JAR and RABBITMQ_CLIENT_JAR' >&2; exit 1; }
BUILD="$ROOT/build"
rm -rf "$BUILD/classes" "$BUILD/modules" "$BUILD/dist"
mkdir -p "$BUILD/classes" "$BUILD/modules" "$BUILD/dist"
compile() {
  local name="$1" deps="$2"; shift 2
  mkdir -p "$BUILD/modules/$name"
  # Arrays prevent javac from receiving shell glob literal on a missing source directory.
  mapfile -t sources < <(find "$ROOT/$name/src/main/java" -name '*.java' | sort)
  (( ${#sources[@]} > 0 )) || { echo "No sources for $name" >&2; exit 1; }
  if [[ -z "$deps" ]]; then javac -encoding UTF-8 -d "$BUILD/modules/$name" "${sources[@]}"
  else javac -encoding UTF-8 -cp "$deps" -d "$BUILD/modules/$name" "${sources[@]}"; fi
  jar --create --file "$BUILD/dist/$name.jar" -C "$BUILD/modules/$name" .
}
CORE="$BUILD/dist/core.jar"
STORE="$BUILD/dist/store-postgres.jar"
ADAPTER="$BUILD/dist/adapter-httpjdk.jar"
SHIM="$BUILD/dist/lab-slf4j-shim.jar"
compile core ''
compile store-postgres "$CORE:$PGJAR"
compile adapter-httpjdk "$CORE"
mkdir -p "$BUILD/modules/lab-slf4j-shim"
javac -encoding UTF-8 -d "$BUILD/modules/lab-slf4j-shim" "$ROOT"/lab/slf4j-test-only/org/slf4j/*.java
jar --create --file "$SHIM" -C "$BUILD/modules/lab-slf4j-shim" .
compile runtime-rabbit "$CORE:$STORE:$ADAPTER:$SHIM:$PGJAR:$AMQPJAR"
compile lab-java-providers "$PGJAR"
for name in core store-postgres adapter-httpjdk lab-slf4j-shim runtime-rabbit lab-java-providers; do
  cp -a "$BUILD/modules/$name/." "$BUILD/classes/"
done
printf 'Built %s (separate jars with enforced compile-time dependencies)\n' "$BUILD/dist"
