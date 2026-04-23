# paperslice

일본어 신문 PDF를 기사/광고/헤더 단위로 분리하는 FastAPI 서비스.
Page-accurate provenance, 이미지 자산 보존 지원. MinerU(+PyMuPDF) 기반.

이 저장소의 `claude/cross-platform-docker-fi22j` 브랜치는 원본 v8(Windows에서
검증됨)을 **macOS / Linux에서도 동일하게 빌드·실행**할 수 있도록 정리한 판입니다.

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

## 주요 엔드포인트

| Method | Path | 역할 |
|---|---|---|
| `GET` | `/health` | liveness probe |
| `GET` | `/info` | 런타임 메타 (버전, GPU 유무 등) |
| `POST` | `/parse` | PDF 업로드 → 구조화된 기사 JSON |
| `GET` | `/documents/{id}/pages/{n}/blocks` | 원본 MinerU 블록 덤프 |
| `GET` | `/documents/{id}/raw` | MinerU raw artifact 목록 |
| `GET` | `/docs` | OpenAPI / Swagger UI |

`POST /parse`의 주요 폼 필드:

- `file` (필수) — PDF
- `mode` — `auto`(기본) / `ocr` / `txt`
- `reading_direction` — `ltr`(기본) / `rtl` (일본·중국 신문)
- `language` — OCR 언어 (`japan`, `korean`, `en`, …)
- `diff_report` — true면 ocr/txt 둘 다 돌려 차이 리포트 포함 (2배 느림)

자세한 필드는 `/docs`에서 확인.

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
│   ├── main.py                 # FastAPI 엔트리
│   ├── pipeline.py             # [1/8] ~ [8/8] 파이프라인
│   ├── pdf_type_detector.py    # 세로쓰기/스캔 자동 판별 (v8 신규)
│   ├── mineru_runner.py        # MinerU CLI 호출
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
│   └── test_diff_builder.py
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

## 배포 가이드

v7 → v8 업그레이드 절차는 [`DEPLOY_v8.md`](./DEPLOY_v8.md) 참고. Windows(PowerShell)과
macOS/Linux(bash) 양쪽 절차를 병기.
