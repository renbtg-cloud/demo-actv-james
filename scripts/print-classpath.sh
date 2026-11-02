#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PGJAR="${POSTGRES_JAR:-/mnt/data/postgresql-42.7.14.jar}"
AMQPJAR="${RABBITMQ_CLIENT_JAR:-/mnt/data/amqp-client-5.37.0.jar}"
printf '%s' "$ROOT/build/dist/core.jar:$ROOT/build/dist/store-postgres.jar:$ROOT/build/dist/adapter-httpjdk.jar:$ROOT/build/dist/runtime-rabbit.jar:$ROOT/build/dist/lab-slf4j-shim.jar:$PGJAR:$AMQPJAR"
