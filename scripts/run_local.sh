#!/usr/bin/env bash
# Run paperslice locally on macOS / Linux.
#
# Usage:
#   scripts/run_local.sh                  # port 8100, ./output 마운트
#   PORT=9000 scripts/run_local.sh        # 포트 변경
#   PORT=8000 scripts/run_local.sh        # v8 구버전 호환 (호스트만 8000 으로 노출)
#   IMAGE_TAG=paperslice:gpu scripts/run_local.sh --gpus all

set -euo pipefail

IMAGE_TAG="${IMAGE_TAG:-paperslice:latest}"
# v9: 컨테이너 내부 포트 8000 → 8100 (이슈 #2). 호스트 포트는 PORT env 로 override.
PORT="${PORT:-8100}"
OUTPUT_DIR="${OUTPUT_DIR:-$(pwd)/output}"

mkdir -p "$OUTPUT_DIR"

# 나머지 인자는 docker run에 그대로 전달 (--gpus all 같은 옵션)
EXTRA_ARGS=("$@")

echo "[run_local.sh] Image : $IMAGE_TAG"
echo "[run_local.sh] Port  : $PORT -> 8100"
echo "[run_local.sh] Output: $OUTPUT_DIR -> /app/output"

docker run --rm \
  -p "${PORT}:8100" \
  -v "${OUTPUT_DIR}:/app/output" \
  "${EXTRA_ARGS[@]}" \
  "$IMAGE_TAG"
