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
    PAPERSLICE_SCRATCH_ROOT=/tmp/paperslice-scratch

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

# --- User & runtime dirs ---
RUN useradd --create-home --home-dir /home/paperslice --shell /usr/sbin/nologin paperslice && \
    mkdir -p /app/output /tmp/paperslice-scratch /home/paperslice/.cache && \
    chown -R paperslice:paperslice /app /home/paperslice /tmp/paperslice-scratch

ENV HF_HOME=/home/paperslice/.cache/huggingface \
    MODELSCOPE_CACHE=/home/paperslice/.cache/modelscope

USER paperslice
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=5 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["uvicorn", "paperslice.main:app", "--host", "0.0.0.0", "--port", "8000"]
