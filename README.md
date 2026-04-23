# paperslice

**신문 PDF를 기사 단위로 잘라 구조화된 JSON으로 뽑아내는 파서.**

스캔된 종이 신문과 디지털 신문 PDF 모두를 입력으로 받아, 한 페이지 위에 겹쳐
배치된 여러 기사·광고·헤더를 자동으로 분리합니다. 각 기사는 *어느 페이지의 어느
영역에서 나왔는지*(page + bbox)까지 기록되어 있어, 후속 분석이나 원본 대조가
그대로 가능합니다.

내부적으로 [MinerU](https://github.com/opendatalab/MinerU)(pipeline / vlm / hybrid)
+ [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) + [PyMuPDF](https://pymupdf.readthedocs.io)
를 오케스트레이션하며, FastAPI로 HTTP 인터페이스를 제공합니다.

### 이런 문제를 풉니다
- **한 페이지 = 여러 기사**인 신문을 기사 단위로 분리해야 할 때
- 스캔본/디지털 PDF가 섞여 있어서 **파이프라인을 하나로 통일**해야 할 때
- 세로쓰기 일본·중국 신문에서 MinerU `-m txt` 경로의 글자 누락 버그를 회피하고 싶을 때
- 광고·헤더·목차를 본문과 **자동 구분**해서 기사 텍스트만 LLM에 넘기고 싶을 때
- 추출된 이미지를 **영구 저장**하고 URL로 참조하고 싶을 때

### 주요 기능
- **다국어 OCR** — 한국어 / 일본어 / 중국어(간·번체) / 영어 등 PaddleOCR이 지원하는 언어 전부.
- **세로쓰기 자동 감지 (v8)** — PyMuPDF로 PDF를 먼저 살펴 세로쓰기·스캔본이면 자동으로 OCR 경로로 분기.
- **CPU 자동 튜닝 (v9)** — 컨테이너 기동 시 cgroup / CPU affinity 를 탐지해 OMP/MKL/OpenBLAS/torch 스레드를 [2, 8] 로 자동 캡. vCPU 폭주로 인한 메모리 터짐 방지.
- **페이지 단위 청킹 (v9)** — 큰 PDF(기본 10페이지 초과)를 5페이지 단위로 쪼개 MinerU 를 여러 번 호출. 한 호출의 피크 메모리가 chunk 크기에 비례해 내려가 **CPU-only 8GB 박스에서도 119페이지 PDF 완주** 가능.
- **OOM 자동 재시도 (v9)** — MinerU stderr 에서 `OutOfMemoryError` / `Killed` / `RemoteDisconnected` / `ConnectionReset` 등이 감지되면 `MINERU_VIRTUAL_VRAM_SIZE` 를 반감해 재시도.
- **기사 단위 세그멘테이션** — column 검출 후 헤드라인-본문-이미지를 한 노드로 묶음.
- **Provenance 보존** — 모든 텍스트 블록과 이미지에 원본 페이지 번호와 bbox가 그대로 붙음.
- **이미지 자산 관리** — 추출된 이미지를 `output/<document_id>/images/` 에 영구 저장 + 다운로드 API.
- **OCR vs 텍스트-레이어 diff** — `diff_report=true`로 두 방식을 돌려 품질 차이 검증.
- **CPU / GPU 양쪽 지원** — `Dockerfile`(CPU, pipeline) / `Dockerfile.gpu`(CUDA, vlm/hybrid).
- **Cross-platform Docker** — macOS / Linux / Windows에서 동일한 `docker compose up --build`.

### 이 브랜치 (`claude/cross-platform-docker-fi22j`) 에서 한 것
원본 v8은 Windows에서만 검증되어 있었습니다. 이 브랜치는 소스 변경 없이 빌드·실행·
문서를 손봐 **macOS / Linux / Windows 동일 명령어로 돌아가게** 만든 판입니다
(`.gitattributes` 라인엔딩 정규화, bash/PowerShell 스크립트 페어, Apple Silicon 자동 `--platform=linux/amd64`, 상대경로 `docker-compose.yml` 등).

---

## 빠른 시작

### 공통 전제
- Docker Desktop (Windows/macOS) 또는 Docker Engine (Linux)
- 8000 포트 사용 가능

### macOS / Linux

```bash
git clone -b claude/cross-platform-docker-fi22j \
  https://github.com/MustangYun/paper_slice.git
cd paper_slice

# 빌드 + 실행 (가장 간단)
docker compose up --build
# → http://localhost:8000/docs 로 접속
```

스크립트를 직접 쓰고 싶다면:

```bash
./scripts/build.sh              # 이미지 빌드
./scripts/run_local.sh          # 컨테이너 실행
```

### Windows (PowerShell)

```powershell
git clone -b claude/cross-platform-docker-fi22j `
  https://github.com/MustangYun/paper_slice.git
cd paper_slice

docker compose up --build
# 또는
.\scripts\build.ps1
.\scripts\run_local.ps1
```

---

## API 엔드포인트

| Method | Path | Tag | 역할 |
|---|---|---|---|
| `GET` | `/health` | meta | liveness probe. 항상 `{"status":"ok"}` |
| `GET` | `/info` | meta | 버전, GPU 유무 등 런타임 메타데이터 |
| `POST` | `/parse` | core | **PDF 업로드 → 구조화된 기사 JSON** (주 엔드포인트) |
| `GET` | `/documents/{id}/pages/{n}/blocks` | debug | 특정 페이지의 원본 MinerU 블록 덤프 |
| `GET` | `/documents/{id}/raw` | debug | 이 문서의 MinerU raw artifact 목록 |
| `GET` | `/documents/{id}/raw/{file_path}` | debug | 개별 raw artifact 다운로드 |
| `GET` | `/docs` | — | OpenAPI / Swagger UI (모든 파라미터 설명 포함) |
| `GET` | `/redoc` | — | ReDoc UI (읽기용) |
| `GET` | `/openapi.json` | — | OpenAPI 스키마 원본 |

---

## 파라미터 레퍼런스

> 💡 동일한 설명이 **`/docs` Swagger UI에도 자동으로 나옵니다** — FastAPI가 아래
> 정의를 OpenAPI 스키마로 변환하기 때문입니다. 필드 옆의 `i` 아이콘을 클릭하면 바로 읽을 수 있어요.

### `POST /parse` — Form 필드

`Content-Type: multipart/form-data`. 모든 값 필드는 form 필드(JSON body 아님)로 전송.

| 이름 | 타입 | 필수 | 기본값 | 설명 |
|---|---|---|---|---|
| **`file`** | `File` (PDF) | ✅ 필수 | — | 파싱할 PDF 파일. 확장자 `.pdf` 필수. 최대 `100 MB` (초과 시 `413`). |
| **`backend`** | enum | 선택 | 서버의 `default_backend` (보통 `pipeline`) | MinerU 백엔드 선택. [`pipeline` / `vlm` / `hybrid`](#backend-옵션-상세) |
| **`language`** | string | 선택 | 서버의 `default_language` (보통 `japan`) | OCR 언어 코드. 아래 표 참고. |
| **`mode`** | enum | 선택 | `auto` | 파싱 방식. `pipeline` 백엔드에서만 의미 있음. [상세](#mode-옵션-상세) |
| **`diff_report`** | bool | 선택 | `false` | OCR vs 텍스트 레이어 비교 리포트 포함 여부. **true면 시간 2배.** |
| **`reading_direction`** | enum-like string | 선택 | `ltr` | 기사 column 읽는 방향. `ltr` 또는 `rtl`. [상세](#reading_direction-옵션-상세) |

#### `backend` 옵션 상세

| 값 | 하드웨어 | 정확도 | 속도 | 용도 |
|---|---|---|---|---|
| `pipeline` *(기본)* | CPU OK | ~82%+ | 빠름 | 일반적인 경우. 대부분 이걸 쓰세요. |
| `vlm` | **GPU 필수** | ~90%+ | 느림, VRAM 많음 | 고정밀이 필요할 때. 비전-언어 모델. |
| `hybrid` | **GPU 필수** | ~88%+ | 중간 | pipeline + vlm 혼합. |

GPU가 없는데 `vlm`/`hybrid`를 요청하면:
- `PAPERSLICE_STRICT_GPU=true`: `500 mineru_failure`로 즉시 실패.
- `PAPERSLICE_STRICT_GPU=false` *(기본)*: 경고 로그 찍고 `pipeline`으로 **조용히 폴백**. 응답 `quality.warnings[]`에 사유가 들어감.

#### `language` 옵션 상세

MinerU가 그대로 PaddleOCR에 전달하는 언어 코드.

| 값 | 대상 |
|---|---|
| `japan` *(기본)* | 일본어 + 한자 |
| `korean` | 한국어 |
| `en` | 영어 |
| `ch` | 중국어 (간체 + 번체 공용) |
| `fr`, `de`, `es`, `pt`, `ru`, `ar`, ... | PaddleOCR이 지원하는 기타 언어 |

**주의**: 여러 언어가 섞인 문서면 **지배적인 하나**만 지정. 잘못 지정 시 OCR 품질이 크게 떨어집니다.

#### `mode` 옵션 상세

| 값 | 동작 | 언제 쓰는지 |
|---|---|---|
| `auto` *(기본)* | PyMuPDF로 PDF를 1회 살펴서 `ocr`/`txt` 자동 선택. 스캔본 또는 세로쓰기 감지 시 `ocr`. | **거의 항상 이걸 쓰세요.** |
| `ocr` | 무조건 OCR. | 스캔 PDF 확실, 또는 auto가 잘못 판별하는 예외 케이스. |
| `txt` | PDF 텍스트 레이어 직접 추출. 빠르고 정확. | **디지털 가로쓰기 PDF 확실**할 때만. ⚠️ 세로쓰기에 쓰면 글자 누락(MinerU 버그). |

`vlm`/`hybrid` 백엔드에서는 `mode`가 무시됨.

#### `reading_direction` 옵션 상세

| 값 | 설명 | 예시 |
|---|---|---|
| `ltr` *(기본)* | left-to-right — 왼쪽 column부터 | 한국 신문/논문, 영문 보고서, 대부분의 디지털 문서 |
| `rtl` | right-to-left — 오른쪽 column부터 | 일본·중국·대만 **전통 신문** (세로쓰기 + 오른쪽→왼쪽) |

**영향 범위**: 기사 순서와 "가로 헤드라인 ↔ 아래 본문 column" 매칭에 영향. 한 column 안의 텍스트 순서는 방향과 무관하게 동일.

잘못된 값(`ltr`/`rtl` 외)을 넣으면 `400 Bad Request`.

---

### `GET /documents/{document_id}/pages/{page_number}/blocks` — Path 파라미터

| 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `document_id` | string | ✅ | `POST /parse` 응답의 `document_id`. `/`, `\\`, `..` 포함 시 400. |
| `page_number` | int | ✅ | **1-based** 페이지 번호. 1 미만이면 400. |

### `GET /documents/{document_id}/raw` — Path 파라미터

| 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `document_id` | string | ✅ | `POST /parse` 응답의 `document_id`. |

### `GET /documents/{document_id}/raw/{file_path}` — Path 파라미터

| 이름 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `document_id` | string | ✅ | `POST /parse` 응답의 `document_id`. |
| `file_path` | string | ✅ | `GET /documents/{id}/raw` 응답의 `relative_path`. `..` 시작 또는 `/`로 시작하면 400. |

---

### 서버 설정 (환경변수)

컨테이너 기동 시 변경 가능. 모두 `PAPERSLICE_` prefix.

#### 기본

| 변수 | 기본값 | 설명 |
|---|---|---|
| `PAPERSLICE_DEFAULT_BACKEND` | `pipeline` | 요청에 `backend` 없을 때 쓸 값 |
| `PAPERSLICE_DEFAULT_LANGUAGE` | `japan` | 요청에 `language` 없을 때 쓸 값 |
| `PAPERSLICE_STRICT_GPU` | `false` | true면 GPU 백엔드 요청에 GPU 없으면 에러. false면 조용히 폴백. |
| `PAPERSLICE_OUTPUT_ROOT` | `/app/output` | 컨테이너 내부의 영구 출력 경로. 보통 볼륨 마운트. |
| `PAPERSLICE_SCRATCH_ROOT` | `/tmp/paperslice-scratch` | 요청별 임시 작업 디렉터리. 끝나면 삭제. |
| `PAPERSLICE_MAX_UPLOAD_MB` | `100` | PDF 업로드 최대 크기. |
| `PAPERSLICE_MINERU_TIMEOUT_SEC` | `1800` | MinerU 1회 실행 타임아웃 (초). 첫 실행 시 모델 다운로드가 있어서 길게 잡음. |
| `PAPERSLICE_CORS_ALLOW_ORIGINS` | `["*"]` | CORS allowed origins. 운영 환경에서는 프론트 도메인으로 좁히세요. |
| `PAPERSLICE_MINERU_BIN` | `mineru` | MinerU CLI 바이너리 경로. |

#### CPU 튜닝 (v9 신규)

| 변수 | 기본값 | 설명 |
|---|---|---|
| `PAPERSLICE_CPU_THREADS` | *auto* | OMP/MKL/OpenBLAS/NUMEXPR/torch 스레드 상한. 미지정 시 cgroup → sched_getaffinity → cpu_count 순으로 탐지, `[2, 8]` 로 클램프. |
| `PAPERSLICE_CHUNK_PAGES` | `5` | MinerU 1회 호출당 페이지 수. 낮출수록 피크 메모리 ↓, 서브프로세스 오버헤드 ↑. `0` 으로 주면 청킹 비활성화(과거 동작). |
| `PAPERSLICE_CHUNK_THRESHOLD_PAGES` | `10` | 이 페이지 수를 **초과** 할 때만 청킹을 적용. 짧은 PDF 는 분할 오버헤드 회피. |

#### MinerU 튜닝 (v9 신규)

| 변수 | 기본값 | 설명 |
|---|---|---|
| `PAPERSLICE_MINERU_DEVICE_MODE` | `cpu` | `MINERU_DEVICE_MODE` 로 전달. CPU 경로 강제. |
| `PAPERSLICE_MINERU_VIRTUAL_VRAM_GB` | `1` | `MINERU_VIRTUAL_VRAM_SIZE` 로 전달. MinerU 내부 `window_size` / 배치 비율을 결정. 8GB RAM 박스 기준 `1` 이 안전, 16GB+ 이면 `2` 까지. |
| `PAPERSLICE_MINERU_MODEL_SOURCE` | `modelscope` | `MINERU_MODEL_SOURCE`. `modelscope` / `huggingface`. 리전별 속도 차이. |
| `PAPERSLICE_MINERU_FORMULA_ENABLE` | `false` | `MINERU_FORMULA_ENABLE`. 수식 검출은 CPU에서 가장 비싼 feature — 기본 off. |
| `PAPERSLICE_MINERU_TABLE_ENABLE` | `true` | `MINERU_TABLE_ENABLE`. |
| `PAPERSLICE_MINERU_RETRY_ON_OOM` | `1` | OOM/연결 리셋 stderr 감지 시 재시도 횟수. 매 재시도마다 `virtual_vram_gb` 를 반감(최소 1). |

예:
```bash
# 사용량이 적은 공유 서버에서 타임아웃 줄이고 업로드 제한 올리기
docker run --rm -p 8000:8000 \
  -e PAPERSLICE_MAX_UPLOAD_MB=200 \
  -e PAPERSLICE_MINERU_TIMEOUT_SEC=600 \
  -e PAPERSLICE_STRICT_GPU=true \
  paperslice:latest

# 16GB RAM / 8 vCPU 박스에서 throughput 올리기 (v9)
docker run --rm -p 8000:8000 \
  --cpus=8 --memory=16g \
  -e PAPERSLICE_CPU_THREADS=6 \
  -e PAPERSLICE_MINERU_VIRTUAL_VRAM_GB=2 \
  -e PAPERSLICE_CHUNK_PAGES=10 \
  paperslice:latest

# 4GB 초저메모리 박스에서 안정화 — chunk 더 작게, vram 더 낮게
docker run --rm -p 8000:8000 \
  --cpus=2 --memory=4g \
  -e PAPERSLICE_CPU_THREADS=2 \
  -e PAPERSLICE_MINERU_VIRTUAL_VRAM_GB=1 \
  -e PAPERSLICE_CHUNK_PAGES=3 \
  -e PAPERSLICE_MINERU_FORMULA_ENABLE=false \
  paperslice:latest
```

---

## 프로젝트 구조

```
.
├── Dockerfile                  # CPU 이미지 (python:3.12-slim + uv + MinerU pipeline)
├── docker-compose.yml          # 로컬 개발용. OS 무관.
├── pyproject.toml              # Python 의존성 (pymupdf 포함)
├── .gitattributes              # CRLF→LF 자동 정규화 (Windows 호환)
├── .dockerignore
├── src/paperslice/
│   ├── __init__.py             # __version__
│   ├── main.py                 # FastAPI 엔트리 (v9: 기동 시 CPU 스레드 캡 적용)
│   ├── pipeline.py             # [1/8] ~ [8/8] 파이프라인 (v9: 페이지 청킹 + 병합)
│   ├── pdf_type_detector.py    # 세로쓰기/스캔 자동 판별 (v8)
│   ├── mineru_runner.py        # MinerU CLI 호출 (v9: env 주입 + OOM 재시도)
│   ├── cpu_tuning.py           # CPU 코어 자동 탐지 + MinerU env 조립 (v9 신규)
│   ├── pdf_chunker.py          # PDF 페이지 단위 분할 + 결과 병합 (v9 신규)
│   ├── diff_builder.py         # ocr vs txt 비교
│   ├── schemas.py              # Pydantic 응답 모델
│   ├── config.py               # 환경변수 기반 설정 (PAPERSLICE_*)
│   ├── block_enricher.py       # raw MinerU 블록 → EnrichedBlock 정규화
│   ├── classifier.py           # 블록 역할(headline/body/ad/header) 분류
│   ├── segmenter.py            # column 인식 + 기사 단위 묶기
│   ├── asset_manager.py        # 이미지 영구 저장
│   └── utils/
│       ├── bbox.py             # bbox 연산 (IoU, union 등)
│       ├── columns.py          # column 검출
│       ├── location.py         # 페이지 내 좌표 → 사람용 라벨
│       └── logging.py          # 로깅 셋업
├── tests/
│   ├── test_pdf_type_detector.py
│   ├── test_diff_builder.py
│   ├── test_cpu_tuning.py      # v9 신규 (CPU 탐지/env 조립/OOM 마커)
│   └── test_pdf_chunker.py     # v9 신규 (분할/오프셋/이미지 병합)
├── scripts/
│   ├── build.sh / build.ps1
│   └── run_local.sh / run_local.ps1
└── docker/
    └── entrypoint.sh
```

---

## Cross-platform 관련 주의

- **줄바꿈**: `.gitattributes`가 `.sh`/`Dockerfile`/`.py`를 LF로 강제. Windows에서
  편집해도 컨테이너 안에서 `bash\r` 에러 안 납니다.
- **볼륨 마운트**: `docker-compose.yml`은 `./output` 상대경로를 쓰므로 OS별
  문법 차이 없음. 스크립트는 각 OS에 맞는 문법(`$(pwd)` / `${PWD}`) 사용.
- **Apple Silicon**: `scripts/build.sh`가 arm64 Mac을 감지하면 자동으로
  `--platform=linux/amd64`를 붙입니다 (MinerU는 amd64에서만 검증됨).

---

## 사내망 / Corporate CA 설정

집이나 일반 클라우드에서는 이 섹션을 **건너뛰어도 됩니다**. 빌드가 SSL 에러로
실패하거나 (`certificate verify failed`, `self-signed certificate`), 사내 zscaler /
Bluecoat / Netskope / 방화벽 프록시 뒤에 있을 때만 필요합니다.

### 필요한 파일
사내 보안팀 또는 IT가 배포하는 **root CA 인증서 파일**. 확장자는 보통 `.crt` 또는 `.pem`.
파일 이름 예: `corp-root-ca.crt`, `zscaler-root.crt`, `company-mitm.pem` 등.

> ⚠️ `.pem` 파일이라면 `.crt`로 확장자만 바꿔서 넣으세요 (`update-ca-certificates`가
> `.crt` 확장자만 인식). 내용 형식은 둘 다 PEM(`-----BEGIN CERTIFICATE-----` 로 시작)이면 동일.

### 넣는 위치
프로젝트 루트 바로 아래 `certs/` 폴더. 폴더가 없으면 새로 만드세요.

**macOS / Linux**
```bash
mkdir -p certs
cp ~/Downloads/corp-root-ca.crt certs/
# 여러 개여도 OK — certs/ 안의 모든 .crt가 주입됩니다.
ls certs/
# corp-root-ca.crt  zscaler-root.crt
```

**Windows (PowerShell)**
```powershell
New-Item -ItemType Directory -Force .\certs | Out-Null
Copy-Item "$HOME\Downloads\corp-root-ca.crt" .\certs\
Get-ChildItem .\certs
```

### 실행 구조
```
paper_slice/
├── certs/                    ← 여기!
│   ├── corp-root-ca.crt
│   └── zscaler-root.crt
├── Dockerfile
├── ...
```

> 🔒 `certs/`는 `.gitignore`에 등록돼 있어 실수로 공개 저장소에 올라가지 않습니다.
> `git check-ignore -v certs` 로 확인 가능.

### 빌드 시 CA 주입

**docker compose (가장 간단)**
```bash
# macOS / Linux / Windows 동일
WITH_CORP_CA=1 docker compose up --build
```

**스크립트**
```bash
# macOS / Linux
./scripts/build.sh --corp-ca
./scripts/run_local.sh
```
```powershell
# Windows
.\scripts\build.ps1 -CorpCa
.\scripts\run_local.ps1
```

빌드 로그에 `update-ca-certificates: added N new CA certificates ...` 가 나오면 성공.
`pip`, `curl`, `requests`, `urllib3`, `httpx` 모두 주입된 CA를 자동으로 씁니다
(Dockerfile의 `REQUESTS_CA_BUNDLE` / `SSL_CERT_FILE` / `CURL_CA_BUNDLE` / `PIP_CERT` ENV 덕).

### 문제 해결
| 증상 | 원인 | 해결 |
|---|---|---|
| `SSL: CERTIFICATE_VERIFY_FAILED` | CA 미주입 | `WITH_CORP_CA=1`로 재빌드 |
| `certs/` 넣었는데 여전히 에러 | 확장자가 `.pem` | `.crt`로 변경 후 재빌드 |
| 재빌드 안 되는 듯 | 캐시된 레이어 사용 | `docker compose build --no-cache` |

---

## 실행하고 테스트하기

### 1) 서버 실행

```bash
docker compose up --build
```
성공 로그:
```
paperslice  | INFO:     Uvicorn running on http://0.0.0.0:8000
paperslice  | INFO:     Application startup complete.
```

### 2) `/docs` (Swagger UI)에서 인터랙티브 테스트

브라우저로 **http://localhost:8000/docs** 열기.

1. `POST /parse` 패널을 클릭 → **Try it out**
2. `file` 슬롯에 PDF 파일 업로드 (드래그 또는 Choose File)
3. 다른 필드는 선택:
   - `mode`: `auto` (권장) / `ocr` / `txt`
   - `language`: `japan` / `korean` / `en` 등
   - `reading_direction`: 일본 신문은 `rtl`, 나머지는 `ltr`
   - `diff_report`: 디버깅 필요할 때만 `true`
4. **Execute** 클릭
5. **Response body**에 구조화된 JSON이 나옵니다. 아래로 스크롤하면 `curl` 탭도 같이 보여줘서 복붙해 재현 가능.

> 💡 업로드한 PDF의 변환 결과(이미지, raw MinerU artifact)는 호스트의 `./output/<document_id>/`에 영구 저장됩니다.

### 3) 쉘에서 `curl`로 테스트

#### macOS / Linux (bash)

```bash
# 헬스 체크
curl -s http://localhost:8000/health
# → {"status":"ok"}

# 런타임 정보
curl -s http://localhost:8000/info | python3 -m json.tool

# PDF 파싱 (가장 기본)
curl -s -X POST http://localhost:8000/parse \
  -F "file=@/path/to/sample.pdf" \
  | python3 -m json.tool > result.json

# 일본 신문 PDF, 세로쓰기, auto 모드
curl -s -X POST http://localhost:8000/parse \
  -F "file=@/path/to/nikkan.pdf" \
  -F "language=japan" \
  -F "reading_direction=rtl" \
  -F "mode=auto" \
  | python3 -m json.tool > nikkan_result.json

# 한국어 논문, txt 강제, diff 리포트 포함
curl -s -X POST http://localhost:8000/parse \
  -F "file=@/path/to/paper.pdf" \
  -F "language=korean" \
  -F "mode=txt" \
  -F "diff_report=true" \
  | python3 -m json.tool > paper_result.json

# 파싱 결과에서 document_id만 뽑기
DOC_ID=$(curl -s -X POST http://localhost:8000/parse \
  -F "file=@sample.pdf" | python3 -c "import sys,json;print(json.load(sys.stdin)['document_id'])")
echo "document_id=$DOC_ID"

# 특정 페이지의 raw MinerU 블록 보기
curl -s "http://localhost:8000/documents/$DOC_ID/pages/1/blocks" \
  | python3 -m json.tool

# 그 문서의 raw artifact 목록
curl -s "http://localhost:8000/documents/$DOC_ID/raw" | python3 -m json.tool
```

#### Windows PowerShell

**방법 A: `curl.exe`** (Windows 10 1803+ / 11에 내장. `curl` 별칭이 아니라 **`curl.exe`** 로 호출해야 함 — PowerShell의 `curl`은 `Invoke-WebRequest`라 플래그가 다름)

```powershell
# 헬스 체크
curl.exe -s http://localhost:8000/health

# PDF 파싱
curl.exe -s -X POST http://localhost:8000/parse `
  -F "file=@C:\Users\User-1\Documents\sample.pdf" `
  -F "language=japan" `
  -F "reading_direction=rtl" `
  -o result.json

Get-Content result.json | ConvertFrom-Json | ConvertTo-Json -Depth 10
```

**방법 B: `Invoke-RestMethod`** (PowerShell 네이티브, 7.0+ 권장)

```powershell
# 헬스
Invoke-RestMethod http://localhost:8000/health

# 런타임 정보
Invoke-RestMethod http://localhost:8000/info | ConvertTo-Json -Depth 5

# PDF 파싱 (PS 7.0+에서 -Form 지원)
$response = Invoke-RestMethod `
    -Uri http://localhost:8000/parse `
    -Method POST `
    -Form @{
        file              = Get-Item C:\Users\User-1\Documents\sample.pdf
        language          = 'japan'
        reading_direction = 'rtl'
        mode              = 'auto'
    }
$response | ConvertTo-Json -Depth 10 | Out-File result.json -Encoding utf8
Write-Host "document_id = $($response.document_id)"

# 특정 페이지 블록
$docId = $response.document_id
Invoke-RestMethod "http://localhost:8000/documents/$docId/pages/1/blocks" `
  | ConvertTo-Json -Depth 10
```

#### Windows CMD (`cmd.exe`)

```cmd
:: 헬스
curl -s http://localhost:8000/health

:: PDF 파싱 (CMD는 줄바꿈 이어쓰기 ^)
curl -s -X POST http://localhost:8000/parse ^
  -F "file=@C:\Users\User-1\Documents\sample.pdf" ^
  -F "language=japan" ^
  -F "reading_direction=rtl" ^
  -o result.json

type result.json
```

### 4) 응답 JSON 구조 (요약)

```jsonc
{
  "document_id": "doc-abc123...",
  "source": {
    "filename": "sample.pdf",
    "page_count": 8,
    "parser_backend": "pipeline",
    "mode_used": "ocr",         // auto 판별 결과
    "parsed_at": "2026-04-23T..."
  },
  "pages": [
    {
      "page_number": 1,
      "nodes": [
        {
          "node_id": "p1-art-01",
          "kind": "article",
          "headline": { "text": "...", "provenance": { ... } },
          "body_blocks": [ { "text": "...", "provenance": { ... } } ],
          "images":     [ { "image_id": "p1-img-01", "stored_path": "images/p1-img-01.jpg", ... } ],
          "bbox": { "x0": ..., "y0": ..., "x1": ..., "y1": ... }
        }
      ]
    }
  ],
  "quality": { "status": "success", "warnings": [] },
  "assets_dir": "doc-abc123...",
  "diff_report": null
}
```

자세한 필드 정의는 [`/docs`](http://localhost:8000/docs) 또는 `src/paperslice/schemas.py` 참조.

### 5) 실행 상태 확인 & 정리

```bash
# 컨테이너 상태
docker compose ps

# 로그 실시간 보기
docker compose logs -f paperslice

# 안에서 파이썬 한 줄 테스트
docker compose exec paperslice python -c "import fitz; print(fitz.__doc__[:40])"

# 정지 + 컨테이너/네트워크 제거 (이미지/볼륨은 유지)
docker compose down
```

---

## 단위 테스트 (컨테이너 없이)

MinerU 없이 로직만 검증하려면 로컬 venv에서:

```bash
# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest -v
```
```powershell
# Windows
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
pytest -v
```

---

## CPU 전용 운영 가이드 (v9)

> 대부분의 사용자는 **아무 것도 설정할 필요 없이** 컨테이너를 띄우면 자동으로 CPU 튜닝 + 페이지 청킹이 걸립니다. 이 섹션은 특정 사이즈 박스에서 더 안정적이거나 빠르게 돌리고 싶을 때 참고.

### v9 가 실제로 돌고 있는지 확인하는 법

> ⚠️ v8 이미지를 그대로 띄운 상태에서는 당연히 변화가 없습니다. **반드시 `docker compose build --no-cache` → `docker compose up -d`** 로 새 이미지를 빌드·재기동한 뒤 확인하세요.

```bash
# 1) /info 에 v9 전용 필드가 보이면 성공
curl -s http://localhost:8000/info | python3 -m json.tool
# 기대 출력 (발췌):
#   "cpu_tuning": { "threads": 4, "source": "affinity", "raw_cpu_count": 4 },
#   "mineru_config": {
#     "device_mode": "cpu", "virtual_vram_gb": 1, "chunk_pages": 5, ...
#   }

# 2) 기동 로그에 CPU tuning 한 줄이 찍혀 있으면 성공
docker compose logs paperslice | grep "CPU tuning"
# → CPU tuning: threads=4 (source=cgroup_v2, cpu_count=16)

# 3) /docs 상단 설명에 "### v9 변경 (CPU 최적화)" 블록이 보이면 성공
open http://localhost:8000/docs   # macOS
```

세 가지 중 하나라도 안 보이면 이미지/컨테이너가 구버전입니다.

### 무엇이 자동으로 되나
1. **스레드 캡** — FastAPI 기동 시 cgroup quota / `sched_getaffinity` / `cpu_count` 순으로 가용 CPU 를 탐지해 OMP/MKL/OpenBLAS/NUMEXPR/torch 스레드를 `[2, 8]` 로 클램프. 기동 로그에 1줄 찍힘:
   ```
   CPU tuning: threads=4 (source=cgroup_v2, cpu_count=16)
   ```
2. **페이지 청킹** — PDF 가 `PAPERSLICE_CHUNK_THRESHOLD_PAGES` (기본 10) 를 초과하면 `PAPERSLICE_CHUNK_PAGES` (기본 5) 단위로 쪼개 MinerU 를 여러 번 호출. 병합 시 `page_idx` 오프셋과 이미지 경로 재작성은 자동. 청킹 진행은 `[2/8]` 단계 로그에 찍힘:
   ```
   [2/8] MinerU 시작 → threads=4 (source=cgroup_v2), vram_gb=1, device=cpu, formula=False, chunk_pages=5
   MinerU chunk primary 1/24: pages 1-5 (5 pages)
   MinerU attempt 1/2: threads=4 vram_gb=1 device=cpu
   MinerU chunk primary 1/24 완료: blocks=47 [12.3s]
   ...
   chunk 결과 병합: chunks=24, blocks=1234, images_copied=38
   ```
3. **MinerU CPU env 주입** — `MINERU_DEVICE_MODE=cpu`, `MINERU_VIRTUAL_VRAM_SIZE=1`, `MINERU_FORMULA_ENABLE=false` 등을 subprocess 에 `env=` 로 주입. 운영자가 `os.environ` 을 건드릴 필요 없음.
4. **OOM 자동 재시도** — MinerU stderr 에 `OutOfMemoryError` / `Killed` / `signal 9` / `RemoteDisconnected` / `ConnectionReset` / `Cannot allocate memory` 등이 보이면 `vram_gb` 를 반감(최소 1)해 재시도. 재시도 횟수는 `PAPERSLICE_MINERU_RETRY_ON_OOM` (기본 1).

### 박스 사이즈별 권장값

| 박스 사양 | `CHUNK_PAGES` | `MINERU_VIRTUAL_VRAM_GB` | `CPU_THREADS` | 비고 |
|---|---|---|---|---|
| 4 vCPU / 4 GB | 3 | 1 | 2 | 극소형. formula/table off 권장. |
| **4 vCPU / 8 GB** | **5** *(기본)* | **1** *(기본)* | **auto** *(=4)* | **권장 기본 스펙.** 119 페이지 ≤ 8 분. |
| 8 vCPU / 16 GB | 10 | 2 | 6 | throughput 중심. chunk 크게 해도 OK. |
| 16+ vCPU / 32+ GB | 15 | 4 | 8 | 대량 배치. OS 파일 핸들 상한도 체크. |

### 모델 프리베이크
`Dockerfile` 이 빌드 중 `mineru-models-download` 으로 pipeline 모델을 `/home/paperslice/.cache/` 에 미리 받아둡니다. 네트워크 단절 환경에서 빌드한 경우에만 런타임 첫 요청 시 다운로드로 폴백 — 그때는 `PAPERSLICE_MINERU_TIMEOUT_SEC` 를 넉넉히(기본 1800초) 유지하세요.

### CPU 모드 처리 순서 (요약)
```
요청 도착
  ↓
[1/8] PyMuPDF 로 PDF 타입 감지 (ocr / txt)
  ↓
[2/8] 페이지 수 > 10 ?  ── No → MinerU 1회 호출
         │ Yes
         ↓
       5페이지씩 서브 PDF 생성 (scratch/chunks/)
         ↓
       각 서브 PDF 에 MinerU 순차 실행
         ├─ env: OMP/MKL/...=auto, MINERU_DEVICE_MODE=cpu, VIRTUAL_VRAM=1
         └─ 실패 시 vram_gb //= 2 재시도
         ↓
       content_list 병합 (page_idx 오프셋, 이미지 unique prefix 복사)
  ↓
[3/8] diff 요청 있으면 secondary 도 동일 청킹 흐름
  ↓
[4/8]~[8/8] 블록 enrich → 이미지 저장 → 분류 → 세그먼트 → 응답 조립
```

---

## 문제 해결 (OOM / 느린 파싱)

| 증상 | 원인 | 해결 |
|---|---|---|
| `MinerU exited with code 1` + `RemoteDisconnected` / `ConnectionReset` | MinerU API 서브프로세스가 OOM killer 에게 죽음 | `PAPERSLICE_CHUNK_PAGES` 낮추기 (5 → 3), `PAPERSLICE_MINERU_VIRTUAL_VRAM_GB=1` 유지, `--memory` 제한 올리기 |
| 로그에 `CPU tuning: threads=1` | cgroup 이 vCPU 1개만 할당 | `docker run --cpus=N` 값을 올리거나 K8s `resources.requests.cpu` 상향 |
| 첫 요청이 수 분간 안 끝남 | 모델 프리베이크 실패 → 런타임에 다운로드 중 | 빌드 로그 `WARNING: MinerU 모델 프리베이크 실패` 확인. 네트워크 복구 후 `docker compose build --no-cache` |
| 119 페이지 PDF 가 10 분 넘게 걸림 | chunk 당 MinerU 콜드 시작 오버헤드 | `PAPERSLICE_CHUNK_PAGES` 를 10 으로 올려 서브프로세스 기동 횟수 절반으로 |
| `Killed` 만 stderr 에 찍히고 컨테이너가 OOM | 호스트 메모리 부족 (chunk 크기보다 small) | `--memory` 를 8g 이상 주거나, `PAPERSLICE_MINERU_FORMULA_ENABLE=false` 유지 확인 |
| 스레드 수가 예상보다 많음 | 운영자가 override env 로 큰 값을 줌 | `docker exec <ctr> env \| grep NUM_THREADS` 로 확인. 비었으면 `cpu_tuning` 의 `setdefault` 가 반영한 값 |

디버깅에 도움이 되는 엔드포인트:
```bash
curl -s http://localhost:8000/info
# → {"gpu_available": false, ...}

docker compose logs paperslice 2>&1 | grep -E "CPU tuning|MinerU attempt|chunk"
```

---

## 배포 가이드

- **v8 → v9 업그레이드**: [`DEPLOY_v9.md`](./DEPLOY_v9.md) — CPU 자동 튜닝 + 페이지 청킹 도입.
- **v7 → v8 업그레이드**: [`DEPLOY_v8.md`](./DEPLOY_v8.md) — 세로쓰기 감지 + `[n/8]` 단계 로그 도입.
두 문서 모두 Windows(PowerShell)와 macOS/Linux(bash) 절차를 병기합니다.
