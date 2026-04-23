#!/usr/bin/env bash
# Run paperslice locally on macOS / Linux.
#
# Usage:
#   scripts/run_local.sh                  # port 8000, ./output 마운트
#   PORT=9000 scripts/run_local.sh        # 포트 변경
#   IMAGE_TAG=paperslice:gpu scripts/run_local.sh --gpus all

set -euo pipefail

IMAGE_TAG="${IMAGE_TAG:-paperslice:latest}"
PORT="${PORT:-8000}"
OUTPUT_DIR="${OUTPUT_DIR:-$(pwd)/output}"

mkdir -p "$OUTPUT_DIR"

# 나머지 인자는 docker run에 그대로 전달 (--gpus all 같은 옵션)
EXTRA_ARGS=("$@")

echo "[run_local.sh] Image : $IMAGE_TAG"
echo "[run_local.sh] Port  : $PORT -> 8000"
echo "[run_local.sh] Output: $OUTPUT_DIR -> /app/output"

docker run --rm \
  -p "${PORT}:8000" \
  -v "${OUTPUT_DIR}:/app/output" \
  "${EXTRA_ARGS[@]}" \
  "$IMAGE_TAG"
