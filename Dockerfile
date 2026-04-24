# syntax=docker/dockerfile:1.7
# -------------------------------------------------------------------
# paperslice — CPU image (v8)
# -------------------------------------------------------------------
# v8 변경점:
#   - pip → uv (빌드 속도 5~10배)
#   - pymupdf를 pyproject.toml 기본 의존성에 추가 → 세로쓰기 감지 가능
#
# This image runs MinerU's `pipeline` backend on CPU. Smaller and does
# not need CUDA. Use Dockerfile.gpu if you need the vlm/hybrid backends.

FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PAPERSLICE_OUTPUT_ROOT=/app/output \
    PAPERSLICE_SCRATCH_ROOT=/tmp/paperslice-scratch \
    # CPU 스레드 상한. cpu_tuning.py 가 setdefault 로 반영하므로 운영자가
    # 컨테이너 --cpus 로 더 낮추면 자동으로 그 값까지 내려간다.
    OMP_NUM_THREADS=4 \
    MKL_NUM_THREADS=4 \
    OPENBLAS_NUM_THREADS=4 \
    NUMEXPR_NUM_THREADS=4 \
    NUMEXPR_MAX_THREADS=4 \
    VECLIB_MAXIMUM_THREADS=4 \
    TORCH_NUM_THREADS=4 \
    BLIS_NUM_THREADS=4 \
    TOKENIZERS_PARALLELISM=false \
    # MinerU CPU 기본값. paperslice Settings 에도 같은 값이 있어 둘 중 어느
    # 쪽으로 override 해도 동작한다.
    MINERU_DEVICE_MODE=cpu \
    MINERU_VIRTUAL_VRAM_SIZE=1 \
    MINERU_MODEL_SOURCE=modelscope \
    MINERU_FORMULA_ENABLE=false \
    MINERU_TABLE_ENABLE=true

# System libraries needed by MinerU / OpenCV / Pillow / PyMuPDF.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    libxcb1 \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Corporate CA slot
ARG WITH_CORP_CA=0
COPY certs* /usr/local/share/ca-certificates/
RUN if [ "$WITH_CORP_CA" = "1" ]; then update-ca-certificates; fi

ENV REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    CURL_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    PIP_CERT=/etc/ssl/certs/ca-certificates.crt

# --- uv 설치 (pip 대신 의존성 해결 빠르게) ---
# 공식 이미지에서 정적 바이너리 복사. 시스템에 Python 런타임 영향 없음.
COPY --from=ghcr.io/astral-sh/uv:0.5.7 /uv /uvx /usr/local/bin/

WORKDIR /app

# --- Python dependencies ---
COPY pyproject.toml ./
COPY src ./src
COPY README.md ./

# uv pip install --system: venv 안 만들고 시스템 python에 바로 설치.
# 컨테이너 환경에선 이게 자연스러움.
RUN uv pip install --system ".[mineru]"

# --- Huggingface / ModelScope 캐시 경로 ---
# useradd 전에 ENV 로 선언해야 이어지는 프리베이크 RUN 이 같은 경로를 쓴다.
ENV HF_HOME=/home/paperslice/.cache/huggingface \
    MODELSCOPE_CACHE=/home/paperslice/.cache/modelscope

# --- Pre-bake MinerU pipeline 모델 ---
# 컨테이너 첫 요청에서 수 GB 를 런타임에 당겨오느라 5~8분 SLA 를 깨는 문제 해결.
#
# 빌드 모드 두 가지:
#   BUILD_OFFLINE_TOLERANT=0 (기본) — 프리베이크 필수, 실패 시 빌드도 실패.
#                                    "모델 없는 이미지가 빌드 성공" 불가능.
#   BUILD_OFFLINE_TOLERANT=1 (CI / 폐쇄망) — 실패 허용. 이미지는 완성되지만
#                                    런타임에 모델을 다시 받아야 함.
# 기본 하드페일 이유: 이전 soft-fallback 설계(|| echo WARNING)가 조용히 실패한
# 이미지를 배포했고, 사용자가 런타임에 huggingface.co / modelscope.cn 접근 불가로
# 500 맞던 이슈 다수 (#1, #3, #4, #7, #10).
ARG BUILD_OFFLINE_TOLERANT=0
RUN mkdir -p "$HF_HOME" "$MODELSCOPE_CACHE" && \
    if [ "$BUILD_OFFLINE_TOLERANT" = "1" ]; then \
        echo "INFO: BUILD_OFFLINE_TOLERANT=1 — 프리베이크 실패해도 빌드 계속"; \
        mineru-models-download -s modelscope -m pipeline \
          || echo "WARNING: 모델 프리베이크 실패 — 런타임 다운로드 필요 (HF_HUB_OFFLINE 끄고 실행)"; \
    else \
        echo "INFO: BUILD_OFFLINE_TOLERANT=0 — 프리베이크 필수, 실패 시 빌드 실패"; \
        mineru-models-download -s modelscope -m pipeline \
          && [ -n "$(find "$MODELSCOPE_CACHE" -maxdepth 3 -type f -name '*.safetensors' -o -name '*.pt' -o -name '*.bin' -o -name '*.onnx' 2>/dev/null | head -1)" ] \
          || (echo "FATAL: 프리베이크 실패 또는 캐시가 비어 있음. BUILD_OFFLINE_TOLERANT=1 로 빌드하거나 네트워크 확인 필요."; exit 1); \
    fi

# --- 런타임 오프라인 기본값 ---
# 이미지에 모델이 구워졌으니 런타임에 hub 을 ping 할 이유 없음. huggingface_hub /
# transformers 가 캐시만 쓰게 강제 → (1) 첫 요청 지연 제거 (2) 네트워크 없어도 동작
# (3) 실패 시 "cannot reach hub" 대신 "model not in cache" 명확한 에러.
#
# 런타임 다운로드를 원하면: docker run -e HF_HUB_OFFLINE=0 -e TRANSFORMERS_OFFLINE=0 ...
# 또는 docker-compose.yml 의 environment 블록에서 override.
ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

# --- User & runtime dirs ---
RUN useradd --create-home --home-dir /home/paperslice --shell /usr/sbin/nologin paperslice && \
    mkdir -p /app/output /tmp/paperslice-scratch /home/paperslice/.cache && \
    chown -R paperslice:paperslice /app /home/paperslice /tmp/paperslice-scratch

USER paperslice
# v9: 기본 포트 8000 → 8100 (이슈 #2). 컨테이너 내부는 8100 고정,
# 호스트 매핑은 docker-compose.yml 의 PAPERSLICE_PORT env 로 조정.
EXPOSE 8100

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=5 \
    CMD curl -fsS http://127.0.0.1:8100/health || exit 1

CMD ["uvicorn", "paperslice.main:app", "--host", "0.0.0.0", "--port", "8100"]
