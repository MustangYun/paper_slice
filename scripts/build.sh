#!/usr/bin/env bash
# Build the paperslice Docker image on macOS / Linux.
#
# Usage:
#   scripts/build.sh                 # CPU image, no corporate CA
#   scripts/build.sh --corp-ca       # inject certs from ./certs/
#   scripts/build.sh --gpu           # build the GPU variant (Dockerfile.gpu)
#   IMAGE_TAG=paperslice:dev scripts/build.sh
#
# Apple Silicon 노트:
#   MinerU 공식 이미지는 amd64만 검증됐으므로 arm64 Mac에서는
#   `--platform=linux/amd64`로 빌드해 rosetta/qemu 위에서 돌립니다.
#   속도는 느리지만 결과는 동일.

set -euo pipefail

IMAGE_TAG="${IMAGE_TAG:-paperslice:latest}"
DOCKERFILE="Dockerfile"
WITH_CORP_CA=0
PLATFORM=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --corp-ca)   WITH_CORP_CA=1; shift ;;
    --gpu)       DOCKERFILE="Dockerfile.gpu"; IMAGE_TAG="${IMAGE_TAG%:*}:gpu"; shift ;;
    --platform)  PLATFORM="$2"; shift 2 ;;
    --tag)       IMAGE_TAG="$2"; shift 2 ;;
    -h|--help)
      sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

# Apple Silicon 자동 감지: arm64인데 --platform 미지정이면 amd64로 강제
if [[ -z "$PLATFORM" ]] && [[ "$(uname -s)" == "Darwin" ]] && [[ "$(uname -m)" == "arm64" ]]; then
  PLATFORM="linux/amd64"
  echo "[build.sh] Apple Silicon detected → forcing --platform=$PLATFORM"
fi

BUILD_ARGS=(build -f "$DOCKERFILE" -t "$IMAGE_TAG")
[[ -n "$PLATFORM" ]] && BUILD_ARGS+=(--platform "$PLATFORM")
BUILD_ARGS+=(--build-arg "WITH_CORP_CA=$WITH_CORP_CA" .)

echo "[build.sh] docker ${BUILD_ARGS[*]}"
docker "${BUILD_ARGS[@]}"

echo "[build.sh] Built image: $IMAGE_TAG"
