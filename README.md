# paperslice

**신문 PDF를 기사 단위로 잘라 구조화된 JSON 으로 추출하는 파서.**

[![Release](https://img.shields.io/github/v/release/MustangYun/paper_slice)](https://github.com/MustangYun/paper_slice/releases)
[![Python](https://img.shields.io/badge/python-3.10--3.12-blue)](./pyproject.toml)
[![Docker](https://img.shields.io/badge/docker-ready-blue)](./Dockerfile)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](./pyproject.toml)

스캔된 종이 신문과 디지털 신문 PDF 를 입력받아, **한 페이지에 겹쳐 배치된 여러 기사·광고·헤더를 자동으로 분리**합니다. 각 기사는 *어느 페이지의 어느 영역에서 나왔는지* (page + bbox) 까지 기록되어 후속 분석과 원본 대조가 그대로 가능합니다.

내부적으로 [MinerU](https://github.com/opendatalab/MinerU) + [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) + [PyMuPDF](https://pymupdf.readthedocs.io) 를 오케스트레이션하고 FastAPI 로 HTTP 인터페이스를 제공합니다.

---

## Table of Contents
- [해결하는 문제](#해결하는-문제)
- [주요 기능](#주요-기능)
- [빠른 시작](#빠른-시작) — macOS / Linux / Windows
- [동작 원리](#동작-원리)
- [사용법](#사용법) — Swagger UI / curl / PowerShell / CMD
- [API 엔드포인트](#api-엔드포인트)
- [설정](#설정)
- [사내망 / Corp CA 인증서](#사내망--corp-ca-인증서)
- [문제 해결](#문제-해결)
- [개발](#개발)
- [참고 문서](#참고-문서)

---

## 해결하는 문제

- **한 페이지 = 여러 기사**인 신문을 기사 단위로 분리해야 할 때
- 스캔본/디지털 PDF 가 섞여 있어서 **파이프라인을 하나로 통일**해야 할 때
- 세로쓰기 일본·중국 신문에서 MinerU `-m txt` 경로의 글자 누락 버그를 회피하고 싶을 때
- 광고·헤더·목차를 본문과 **자동 구분**해서 기사 텍스트만 LLM 에 넘기고 싶을 때
- 추출된 이미지를 **영구 저장**하고 URL 로 참조하고 싶을 때

---

## 주요 기능

| 기능 | 내용 |
|---|---|
| 다국어 OCR | 한국어 / 일본어 / 중국어 (간·번체) / 영어 등 PaddleOCR 지원 언어 전체 |
| 세로쓰기 자동 감지 | PyMuPDF 로 PDF 구조 살펴 세로쓰기·스캔본이면 자동으로 OCR 경로 분기 |
| CPU 자동 튜닝 | cgroup / CPU affinity 탐지로 OMP/MKL/OpenBLAS/torch 스레드를 `[2, 8]` 로 자동 캡 |
| 페이지 청킹 | 큰 PDF (기본 10페이지 초과) 는 5페이지 단위로 분할 호출 — 8GB 박스에서도 119 페이지 완주 |
| OOM 자동 재시도 | 실제 OOM 시그니처 감지 시 `MINERU_VIRTUAL_VRAM_SIZE` 반감 재시도 |
| 모델 프리베이크 | Docker 빌드 중 pipeline 모델을 이미지에 구워넣음 — 첫 요청의 수 GB 다운로드 제거 |
| Provenance 보존 | 모든 텍스트 블록과 이미지에 원본 페이지 번호 + bbox 그대로 유지 |
| OCR vs txt diff | `diff_report=true` 로 두 방식 결과를 나란히 비교 |
| Cross-platform Docker | macOS / Linux / Windows 에서 동일 명령 (`docker compose up --build`) |
| 기사 단위 세그멘테이션 | column 검출 후 headline-본문-이미지를 한 노드로 묶음 |
| 이미지 자산 관리 | 추출 이미지를 `output/<document_id>/images/` 에 영구 저장 + 다운로드 API |

---

## 빠른 시작

### 공통 요구사항
- **Docker** — [Docker Desktop](https://www.docker.com/products/docker-desktop/) (macOS/Windows) 또는 Docker Engine (Linux)
- **8100 포트** 사용 가능 — 필요 시 `PAPERSLICE_PORT=8000 docker compose up` 으로 호스트 포트만 변경
- **인터넷 접근** — 사내망/폐쇄망은 [사내망 설정](#사내망--corp-ca-인증서) 참고

### macOS / Linux (bash / zsh)

```bash
git clone https://github.com/MustangYun/paper_slice.git
cd paper_slice
docker compose up --build
# → http://localhost:8100/docs 브라우저로 열기
```

> **Apple Silicon (M1/M2/M3)**: `scripts/build.sh` 가 arm64 를 감지하면 자동으로 `--platform=linux/amd64` 를 붙입니다. Rosetta/qemu 위에서 돌아 속도는 느리지만 결과는 amd64 와 동일.

### Windows (PowerShell)

```powershell
git clone https://github.com/MustangYun/paper_slice.git
cd paper_slice
docker compose up --build
# → http://localhost:8100/docs 브라우저로 열기
```

> `.gitattributes` 가 `.sh`/`Dockerfile` 을 LF 로 강제하니 Windows 에서 편집해도 컨테이너 안에서 `bash\r` 에러 안 납니다.

### 빠른 확인

```bash
curl http://localhost:8100/health
# → {"status":"ok"}
```

브라우저에서 http://localhost:8100/docs 를 열면 Swagger UI 에서 PDF 업로드하고 바로 테스트 가능합니다.

---

## 동작 원리

`POST /parse` 요청이 들어오면 8단계 파이프라인을 탑니다:

```
  PDF 업로드
       │
  [1/8] PyMuPDF 로 PDF 타입 감지 ──► ocr (스캔/세로쓰기) or txt (디지털 가로쓰기)
       │
  [2/8] MinerU 실행
       │     ├─ 페이지 수 > 10 이면 5페이지씩 청킹 후 순차 호출
       │     ├─ stderr 에 OOM 시그니처 감지 → vram 반감 재시도
       │     └─ stderr 에 네트워크 실패 감지 → 즉시 4단계 해결 안내와 함께 실패
       │
  [3/8] (옵션) diff_report=true 시 secondary (txt / ocr 반대쪽) 실행
       │
  [4/8] 블록 enrich — bbox / 페이지 번호 / 텍스트 수준 정규화
       │
  [5/8] 이미지 영구 저장 — output/<document_id>/images/
       │
  [6/8] 블록 분류 — headline / body / ad_text / page_header / toc_index / image / table
       │
  [7/8] column 인식 + 기사 단위 그룹화 — 한 column 안의 headline→body→image 묶음
       │
  [8/8] 응답 JSON 조립 — pages[].nodes[] 구조 + quality.warnings[]
       │
       ▼
   구조화된 JSON
```

모든 요청의 raw MinerU 결과는 `/documents/{id}/raw/*` 로 다시 꺼낼 수 있어 디버깅 시 원본 대조 가능.

---

## 사용법

### 1) Swagger UI (권장, 브라우저)

http://localhost:8100/docs 에서:
1. `POST /parse` 패널 → **Try it out**
2. `file` 슬롯에 PDF 드래그 또는 Choose File
3. 옵션 설정 — `language`, `mode`, `reading_direction`, `diff_report`
4. **Execute** 클릭
5. Response body 에 구조화된 JSON + 복붙 가능한 `curl` 탭

### 2) macOS / Linux (curl)

```bash
# 가장 기본 — auto 모드
curl -s -X POST http://localhost:8100/parse \
  -F "file=@/path/to/sample.pdf" | jq > result.json

# 일본 세로쓰기 신문
curl -s -X POST http://localhost:8100/parse \
  -F "file=@/path/to/nikkei.pdf" \
  -F "language=japan" \
  -F "reading_direction=rtl" | jq > nikkei.json

# 한국어 디지털 논문 — txt 강제 + diff 리포트
curl -s -X POST http://localhost:8100/parse \
  -F "file=@/path/to/paper.pdf" \
  -F "language=korean" \
  -F "mode=txt" \
  -F "diff_report=true" | jq > paper.json
```

> `jq` 가 없으면 `| python3 -m json.tool` 로 대체 가능.

### 3) Windows (PowerShell)

PowerShell 의 `curl` 은 `Invoke-WebRequest` 별칭이라 Linux `curl` 과 플래그가 다릅니다. 두 방법 중 택일:

**방법 A: `curl.exe`** (Windows 10 1803+ / Windows 11 내장)

```powershell
# 헬스 체크
curl.exe -s http://localhost:8100/health

# PDF 파싱
curl.exe -s -X POST http://localhost:8100/parse `
  -F "file=@C:\Users\User-1\Documents\sample.pdf" `
  -F "language=japan" `
  -F "reading_direction=rtl" `
  -o result.json

Get-Content result.json | ConvertFrom-Json | ConvertTo-Json -Depth 10
```

**방법 B: `Invoke-RestMethod`** (PowerShell 7.0+ 네이티브, `-Form` 지원)

```powershell
$response = Invoke-RestMethod `
    -Uri http://localhost:8100/parse -Method POST `
    -Form @{
        file              = Get-Item C:\Users\User-1\Documents\sample.pdf
        language          = 'japan'
        reading_direction = 'rtl'
        mode              = 'auto'
    }
$response | ConvertTo-Json -Depth 10 | Out-File result.json -Encoding utf8
Write-Host "document_id = $($response.document_id)"
```

### 4) Windows (CMD)

```cmd
curl -s http://localhost:8100/health

curl -s -X POST http://localhost:8100/parse ^
  -F "file=@C:\Users\User-1\Documents\sample.pdf" ^
  -F "language=japan" ^
  -F "reading_direction=rtl" ^
  -o result.json

type result.json
```

### 응답 JSON (요약)

```jsonc
{
  "document_id": "doc-abc123...",
  "source": {
    "filename": "sample.pdf",
    "page_count": 8,
    "parser_backend": "pipeline",
    "mode_used": "ocr"
  },
  "pages": [
    {
      "page_number": 1,
      "nodes": [
        {
          "node_id": "p1-art-01",
          "kind": "article",
          "headline":    { "text": "...", "provenance": { ... } },
          "body_blocks": [ { "text": "...", "provenance": { ... } } ],
          "images":      [ { "image_id": "p1-img-01", "stored_path": "images/p1-img-01.jpg", ... } ],
          "bbox":        { "x0": ..., "y0": ..., "x1": ..., "y1": ... }
        }
      ]
    }
  ],
  "quality":     { "status": "success", "warnings": [] },
  "assets_dir":  "doc-abc123...",
  "diff_report": null
}
```

전체 필드 정의는 [`/docs`](http://localhost:8100/docs) 또는 `src/paperslice/schemas.py`.

---

## API 엔드포인트

| Method | Path | 역할 |
|---|---|---|
| `GET` | `/health` | liveness probe — `{"status":"ok"}` |
| `GET` | `/info` | 버전 / GPU 유무 / CPU 튜닝 상태 / MinerU 설정 |
| `POST` | `/parse` | **주 엔드포인트** — PDF → 구조화 JSON |
| `GET` | `/documents/{id}/pages/{n}/blocks` | 특정 페이지의 raw MinerU 블록 |
| `GET` | `/documents/{id}/raw` | 이 문서의 raw artifact 목록 |
| `GET` | `/documents/{id}/raw/{file_path}` | 개별 raw artifact 다운로드 |
| `GET` | `/docs` | Swagger UI (모든 파라미터 상세 설명 포함) |
| `GET` | `/redoc` | ReDoc UI (읽기 전용) |
| `GET` | `/openapi.json` | OpenAPI 스키마 원본 |

---

## 설정

### `POST /parse` 주요 필드

| 필드 | 기본값 | 설명 |
|---|---|---|
| `file` | — | ✅ 필수. PDF 파일, 최대 100 MB |
| `backend` | `pipeline` | `pipeline` (CPU OK) / `vlm` (GPU 필수) / `hybrid` (GPU 필수) |
| `language` | `japan` | `japan` / `korean` / `en` / `ch` / `fr` / ... (PaddleOCR 지원 언어) |
| `mode` | `auto` | `auto` (권장) / `ocr` (스캔 강제) / `txt` (디지털 가로쓰기 강제) |
| `reading_direction` | `ltr` | `ltr` 또는 `rtl` (일본 세로쓰기 신문) |
| `diff_report` | `false` | true = OCR+txt 둘 다 돌려 비교 리포트 포함 (시간 2배) |

### 환경변수 (자주 쓰는 것)

모두 `PAPERSLICE_` prefix. 전체 목록은 `src/paperslice/config.py` 또는 `/info` 응답 참고.

| 변수 | 기본값 | 용도 |
|---|---|---|
| `PAPERSLICE_DEFAULT_BACKEND` | `pipeline` | 요청에 backend 없을 때 |
| `PAPERSLICE_DEFAULT_LANGUAGE` | `japan` | 요청에 language 없을 때 |
| `PAPERSLICE_STRICT_GPU` | `false` | true = GPU 없으면 에러, false = pipeline 폴백 |
| `PAPERSLICE_CPU_THREADS` | *auto* | 스레드 캡. auto 탐지 후 `[2, 8]` 클램프 |
| `PAPERSLICE_CHUNK_PAGES` | `5` | MinerU 호출당 페이지 수 (0 = 청킹 off) |
| `PAPERSLICE_CHUNK_THRESHOLD_PAGES` | `10` | 이 페이지 수 초과 시만 청킹 적용 |
| `PAPERSLICE_MAX_UPLOAD_MB` | `100` | 업로드 최대 크기 |
| `PAPERSLICE_MINERU_TIMEOUT_SEC` | `1800` | MinerU 1회 실행 타임아웃 |

### 박스 사이즈별 권장값

| 박스 사양 | `CHUNK_PAGES` | `VIRTUAL_VRAM_GB` | `CPU_THREADS` | 비고 |
|---|---|---|---|---|
| 4 vCPU / 4 GB | 3 | 1 | 2 | 극소형 — formula off 권장 |
| **4 vCPU / 8 GB** | **5** *(기본)* | **1** *(기본)* | **auto** | **권장 기본 스펙.** 119 페이지 ≤ 8분 |
| 8 vCPU / 16 GB | 10 | 2 | 6 | throughput 중심 |
| 16+ vCPU / 32+ GB | 15 | 4 | 8 | 대량 배치 |

### 동작 확인

CPU 튜닝 + 청킹이 실제 걸렸는지 확인:

```bash
# 1. /info 에 cpu_tuning / mineru_config 필드가 있어야 함
curl -s http://localhost:8100/info | jq '.cpu_tuning, .mineru_config'

# 2. 기동 로그에 CPU tuning 한 줄이 찍혀야 함
docker compose logs paperslice | grep "CPU tuning"
# → CPU tuning: threads=4 (source=cgroup_v2, cpu_count=16)
```

---

## 사내망 / Corp CA 인증서

집이나 일반 클라우드에서는 이 섹션을 **건너뛰어도 됩니다.** 빌드가 SSL 에러 (`certificate verify failed`, `self-signed certificate in chain`) 로 실패하거나, 사내 zscaler / Bluecoat / Netskope 프록시 뒤에 있을 때만 필요합니다.

### 1. 인증서 준비

사내 보안팀이 배포하는 root CA 파일 (`.crt` / `.pem`). 프로젝트 루트 바로 아래 `certs/` 폴더에 배치.

**macOS / Linux**
```bash
mkdir -p certs
cp ~/Downloads/corp-root-ca.crt certs/
# 여러 개여도 OK — certs/ 안의 모든 .crt 가 주입됩니다
ls certs/
```

**Windows (PowerShell)**
```powershell
New-Item -ItemType Directory -Force .\certs | Out-Null
Copy-Item "$HOME\Downloads\corp-root-ca.crt" .\certs\
Get-ChildItem .\certs
```

> ⚠️ `.pem` 파일은 **확장자만** `.crt` 로 바꿔 주세요. `update-ca-certificates` 가 `.crt` 만 인식.
> `certs/` 는 `.gitignore` 에 등록돼 있어 공개 저장소에 실수로 올라가지 않습니다.

### 2. 빌드 시 CA 주입

```bash
# macOS / Linux / Windows 동일
WITH_CORP_CA=1 docker compose up --build
```

빌드 로그에 `update-ca-certificates: added N new CA certificates` 가 나오면 성공. Python 의 `requests` / `urllib3` / `httpx` 와 `pip` / `curl` 모두 주입된 CA 를 자동으로 씁니다.

### 3. 완전 폐쇄망 (모델 다운로드 불가 환경)

빌드 단계에서 modelscope/huggingface 접근이 전혀 안 되는 경우 프리베이크를 skip 하고 이미지를 만들 수 있습니다. 런타임에 다운로드를 시도하므로 런타임엔 네트워크가 필요합니다.

```bash
docker compose build --build-arg BUILD_OFFLINE_TOLERANT=1
docker compose run --rm \
  -e HF_HUB_OFFLINE=0 \
  -e TRANSFORMERS_OFFLINE=0 \
  paperslice
```

---

## 문제 해결

### 빌드 실패

| 증상 | 원인 | 해결 |
|---|---|---|
| `FATAL: 프리베이크 실패 또는 캐시가 비어 있음` | modelscope 접근 실패 (네트워크 또는 SSL 차단) | 사내망이면 `WITH_CORP_CA=1` 추가. 아니면 네트워크 확인 후 `docker compose build --no-cache` |
| `SSL: CERTIFICATE_VERIFY_FAILED` (빌드 중) | Corp CA 미주입 | [사내망 섹션](#사내망--corp-ca-인증서) 따라 `certs/` 배치 후 `WITH_CORP_CA=1` |
| `certs/` 넣었는데 여전히 실패 | 확장자가 `.pem` | `.crt` 로 rename 후 `docker compose build --no-cache` |
| 캐시 때문에 변경이 반영 안 됨 | Docker layer cache | `docker compose build --no-cache` |

### 런타임 실패

| 증상 | 원인 | 해결 |
|---|---|---|
| `MinerU 가 모델 hub 에 접근하지 못해 실패` + 4단계 안내 | 이미지에 모델 없음 또는 런타임 hub 접근 시도 | 메시지 그대로 따라하기 — `BUILD_OFFLINE_TOLERANT=0` 으로 재빌드 / `WITH_CORP_CA=1` 추가 / `-e HF_HUB_OFFLINE=0` 으로 override |
| `MinerU exited with code 1` + `Killed` / `SIGKILL` | 실제 OOM | `PAPERSLICE_CHUNK_PAGES=3`, `--memory=8g` 이상, formula off 확인 |
| 첫 요청이 수 분간 안 끝남 | 프리베이크 실패 → 런타임 다운로드 중 | 빌드 로그에서 프리베이크 성공 확인. 안 됐으면 재빌드 |
| 119 페이지 PDF 가 10 분 이상 | chunk 당 MinerU 콜드 시작 오버헤드 | `PAPERSLICE_CHUNK_PAGES=10` 으로 올려 호출 횟수 절반 |
| `CPU tuning: threads=1` 로그 | cgroup 이 vCPU 1개만 할당 | `docker run --cpus=N` 값 상향 또는 K8s `resources.requests.cpu` |

**디버그 커맨드**:
```bash
curl -s http://localhost:8100/info | jq          # 런타임 메타데이터
docker compose logs paperslice | grep -E "CPU tuning|MinerU attempt|chunk|네트워크"
```

---

## 개발

### 단위 테스트 (Docker 없이)

**macOS / Linux**
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -v
```

**Windows (PowerShell)**
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
pytest -v
```

현재 45 개 테스트 (OOM vs 네트워크 분류 regression 15건 포함).

### 프로젝트 구조

```
.
├── Dockerfile                 # CPU 이미지 (MinerU pipeline, BUILD_OFFLINE_TOLERANT ARG)
├── docker-compose.yml         # 로컬 개발용 - OS 무관
├── pyproject.toml             # Python 의존성
├── src/paperslice/
│   ├── main.py                # FastAPI 엔트리
│   ├── pipeline.py            # [1/8]~[8/8] 단계 오케스트레이션
│   ├── pdf_type_detector.py   # 세로쓰기/스캔 자동 판별
│   ├── mineru_runner.py       # MinerU CLI 호출 + 재시도 + 네트워크 실패 분리
│   ├── cpu_tuning.py          # CPU 자동 탐지 + MinerU env 조립
│   ├── pdf_chunker.py         # 페이지 분할 + 결과 병합
│   ├── block_enricher.py      # raw MinerU 블록 → EnrichedBlock 정규화
│   ├── classifier.py          # 블록 역할 분류 (headline/body/ad/toc/...)
│   ├── segmenter.py           # column 인식 + 기사 단위 묶기
│   ├── asset_manager.py       # 이미지 영구 저장
│   ├── diff_builder.py        # OCR vs txt 비교
│   ├── schemas.py             # Pydantic 응답 모델
│   ├── config.py              # 환경변수 기반 설정
│   └── utils/                 # bbox / columns / location / logging
├── tests/                     # 45개 pytest
├── scripts/
│   ├── build.sh / build.ps1
│   └── run_local.sh / run_local.ps1
└── docker/
    └── entrypoint.sh
```

각 파일의 역할은 파일 상단 docstring 에 더 자세히.

### 기여

브랜치 전략 (`<type>/<slug>` 네이밍), 커밋 메시지 규약, 릴리즈 절차는 [**CONTRIBUTING.md**](./CONTRIBUTING.md) 참고.

---

## 참고 문서

- [**CHANGELOG.md**](./CHANGELOG.md) — 버전별 변경 이력
- [**Releases**](https://github.com/MustangYun/paper_slice/releases) — 태그된 릴리즈
- [**CONTRIBUTING.md**](./CONTRIBUTING.md) — 브랜치/커밋/릴리즈 규약
- [**TODOS.md**](./TODOS.md) — 계획된 후속 작업
- [**DEPLOY_v9.md**](./DEPLOY_v9.md) — v8 → v9 업그레이드 가이드
- [`/docs`](http://localhost:8100/docs) — 실행 중인 서버의 Swagger UI
- [`/info`](http://localhost:8100/info) — 런타임 메타데이터 JSON
