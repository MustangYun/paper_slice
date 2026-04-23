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
- **사내망 CA**: `./certs/` 폴더에 회사 root CA `.crt`를 두고
  `./scripts/build.sh --corp-ca` (또는 `.\scripts\build.ps1 -CorpCa`).

---

## 개발 / 테스트

```bash
# 가상환경에서 직접 테스트하고 싶으면
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\Activate.ps1
pip install -e ".[dev,mineru]"

pytest
```

---

## 배포 가이드

v7 → v8 업그레이드 절차는 [`DEPLOY_v8.md`](./DEPLOY_v8.md) 참고. Windows(PowerShell) 과
macOS/Linux(bash) 양쪽 절차를 병기.
