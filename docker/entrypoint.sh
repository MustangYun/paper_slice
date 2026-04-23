#!/usr/bin/env bash
# Container entrypoint. Optional — Dockerfile uses CMD directly for uvicorn.
# This script is here so you can pre-seed model caches, run DB migrations,
# etc. before starting the server.
set -euo pipefail

: "${PAPERSLICE_HOST:=0.0.0.0}"
: "${PAPERSLICE_PORT:=8000}"

exec uvicorn paperslice.main:app \
  --host "$PAPERSLICE_HOST" \
  --port "$PAPERSLICE_PORT" \
  "$@"
