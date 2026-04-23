"""FastAPI application entrypoint.

Endpoints:
    GET  /health                                   - liveness probe
    GET  /info                                     - runtime info
    POST /parse                                    - upload PDF, get ParseResponse
    GET  /documents/{doc_id}/pages/{n}/blocks     - raw MinerU blocks on a page
    GET  /documents/{doc_id}/raw                   - list raw MinerU artifacts
    GET  /documents/{doc_id}/raw/{filename}        - download a raw artifact
    GET  /docs                                     - OpenAPI Swagger UI

v7 변경점:
- POST /parse에 mode (auto/ocr/txt)와 diff_report (bool) 추가
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from . import __version__
from .config import MineruBackend, settings
from .mineru_runner import MineruError, _gpu_available, get_mineru_version
from .pipeline import parse_pdf
from .schemas import ParseMode, ParseResponse
from .utils.logging import setup_logging

setup_logging()
logger = logging.getLogger(__name__)

app = FastAPI(
    title="paperslice",
    version=__version__,
    description=(
        "**신문 PDF를 기사 단위로 잘라 구조화된 JSON으로 뽑아내는 파서.**\n\n"
        "스캔된 종이 신문과 디지털 신문 PDF 모두를 입력으로 받아, 한 페이지에 "
        "겹쳐 배치된 여러 기사·광고·헤더를 자동으로 분리합니다. 각 기사에는 "
        "*어느 페이지의 어느 영역(bbox)에서 나왔는지* provenance가 붙어 있어 "
        "후속 분석과 원본 대조가 그대로 가능합니다.\n\n"
        "내부적으로 **MinerU**(pipeline / vlm / hybrid 백엔드) + "
        "**PaddleOCR** + **PyMuPDF**를 오케스트레이션합니다.\n\n"
        "### 지원 언어\n"
        "한국어 / 일본어 / 중국어(간·번체) / 영어 등 PaddleOCR이 지원하는 언어 전부. "
        "세로쓰기 일본·중국 신문은 PyMuPDF가 먼저 PDF를 살펴 자동으로 OCR 경로로 분기합니다.\n\n"
        "### 사용 순서\n"
        "1. `POST /parse` 에 PDF 업로드 → `ParseResponse` 수신.\n"
        "2. `assets_dir` 경로에서 추출된 이미지를 가져옴.\n"
        "3. 결과가 의심스러우면 `GET /documents/{id}/pages/{n}/blocks` 로 원본 블록 확인.\n"
    ),
    openapi_tags=[
        {"name": "core", "description": "주요 파싱 API."},
        {"name": "meta", "description": "서비스 상태 / 메타데이터."},
        {"name": "debug", "description": "원본 MinerU artifact 조회 — 결과가 이상할 때 사용."},
    ],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["meta"], summary="Liveness probe")
async def health() -> dict[str, str]:
    """컨테이너가 살아있는지 확인하는 가장 가벼운 엔드포인트.

    쿠버네티스 livenessProbe / Docker HEALTHCHECK 에서 사용.
    항상 `{"status": "ok"}` 를 200으로 반환.
    """
    return {"status": "ok"}


@app.get("/info", tags=["meta"], summary="Runtime metadata")
async def info() -> dict[str, object]:
    """서비스 버전과 런타임 환경 정보.

    반환 필드:

    - `paperslice_version` — 이 서비스의 버전.
    - `mineru_version` — 컨테이너에 설치된 MinerU 버전.
    - `default_backend` — 요청에 `backend`가 없을 때 쓸 기본값.
    - `gpu_available` — `torch.cuda.is_available()` 결과.
    - `strict_gpu` — true일 때 GPU 백엔드 요청에서 CUDA가 없으면 에러.
      false면 조용히 `pipeline`으로 폴백.
    - `output_root` — 파싱 결과물이 영구 저장되는 컨테이너 내부 경로.
    """
    return {
        "paperslice_version": __version__,
        "mineru_version": get_mineru_version(),
        "default_backend": settings.default_backend.value,
        "gpu_available": _gpu_available(),
        "strict_gpu": settings.strict_gpu,
        "output_root": str(settings.output_root),
    }


@app.post(
    "/parse",
    response_model=ParseResponse,
    tags=["core"],
    summary="PDF 업로드 → 구조화된 기사 JSON",
)
async def parse(
    file: Annotated[
        UploadFile,
        File(description=(
            "**필수.** 파싱할 PDF 파일. `multipart/form-data`로 업로드.\n\n"
            "- 확장자는 `.pdf` 여야 함 (대소문자 무관).\n"
            f"- 최대 업로드 크기: `{settings.max_upload_mb} MB` "
            "(초과 시 413 Payload Too Large).\n"
            "- 스캔 PDF, 디지털 PDF, 세로쓰기 일본/중국 신문 모두 지원."
        )),
    ],
    backend: Annotated[
        MineruBackend | None,
        Form(description=(
            "**선택.** MinerU 내부 백엔드 선택.\n\n"
            "- `pipeline` *(기본)* — CPU 친화적. 정확도 약 82+. 대부분의 경우 이걸 쓰세요.\n"
            "- `vlm` — GPU 전용. 비전-언어 모델. 정확도 90+ 하지만 느리고 VRAM 많이 먹음.\n"
            "- `hybrid` — GPU 전용. pipeline + vlm 혼합.\n\n"
            "CUDA가 없는 환경에서 `vlm`/`hybrid`를 요청하면 "
            "`strict_gpu` 설정에 따라 에러 또는 `pipeline`으로 조용히 폴백. "
            "미지정 시 서버의 `PAPERSLICE_DEFAULT_BACKEND` 값 사용 (기본 `pipeline`)."
        )),
    ] = None,
    language: Annotated[
        str | None,
        Form(description=(
            "**선택.** OCR 언어 코드. MinerU가 PaddleOCR에 그대로 전달.\n\n"
            "- `japan` *(기본)* — 일본어 + 한자.\n"
            "- `korean` — 한국어.\n"
            "- `en` — 영어.\n"
            "- `ch` — 중국어 간체 / 번체.\n\n"
            "여러 언어 혼합 문서는 지배적인 언어 하나만 지정. "
            "잘못 지정하면 OCR 품질이 크게 떨어집니다. "
            "미지정 시 서버의 `PAPERSLICE_DEFAULT_LANGUAGE` 값 사용."
        )),
    ] = None,
    mode: Annotated[
        ParseMode | None,
        Form(description=(
            "**선택.** 파싱 방식. `backend=pipeline` 에서만 의미 있음 "
            "(`vlm`/`hybrid`는 내부적으로 알아서 처리).\n\n"
            "- `auto` *(기본)* — PyMuPDF로 PDF를 살펴 자동 결정. "
            "스캔 또는 세로쓰기면 `ocr`, 가로쓰기 디지털이면 `txt`.\n"
            "- `ocr` — 무조건 OCR. 느리지만 스캔본/세로쓰기에 안전.\n"
            "- `txt` — 무조건 텍스트 레이어 추출. 디지털 PDF일 때 가장 빠르고 정확. "
            "세로쓰기에서는 글자 누락 버그가 있으니 사용 금지."
        )),
    ] = None,
    diff_report: Annotated[
        bool,
        Form(description=(
            "**선택. 기본 false.** OCR vs 텍스트 레이어의 비교 리포트 포함 여부.\n\n"
            "- `false` — 선택된 mode로 1번만 실행.\n"
            "- `true` — **MinerU를 2번 실행**(ocr + txt 각각 1회) 후 "
            "bbox 매칭된 블록 간 텍스트 차이를 `diff_report` 필드에 담아 반환.\n\n"
            "⚠️ 시간이 **2배** 걸립니다. 보통 OCR 품질 검증할 때만 켜세요. "
            "`backend=vlm`/`hybrid` 에서는 method 선택 불가라 무시됨."
        )),
    ] = False,
    reading_direction: Annotated[
        str | None,
        Form(description=(
            "**선택. 기본 `ltr`.** 기사 column을 읽는 방향.\n\n"
            "- `ltr` *(left-to-right, 기본)* — 왼쪽 column부터. 한국·서양 신문·대부분의 논문.\n"
            "- `rtl` *(right-to-left)* — 오른쪽 column부터. "
            "일본·중국·대만 전통 신문(세로쓰기).\n\n"
            "이 설정은 **기사 순서**와 **가로 헤드라인 ↔ 본문 column 매칭**에 "
            "영향을 줍니다. 한 column 안의 내용 자체는 방향과 무관하게 동일.\n\n"
            "`ltr`/`rtl` 외 값을 넣으면 400 Bad Request."
        )),
    ] = None,
) -> ParseResponse:
    """PDF를 업로드해서 구조화된 기사 데이터로 반환.

    ### 동작 과정 (8단계)
    1. PDF 타입 감지 (pymupdf로 세로쓰기/스캔 판별).
    2. MinerU 실행 (primary).
    3. `diff_report=true`면 secondary도 실행.
    4. 블록 Enrich (ID, 1-based page 등 붙임).
    5. 이미지 추출/저장 → `output_root/<document_id>/images/`.
    6. 블록 분류 (headline / body / ad / header / …).
    7. Column 검출 + 기사 단위로 묶기.
    8. ParseResponse 조립 + `output_root/<document_id>/result.json`에 저장.

    ### 반환
    `ParseResponse` (schemas.py 참조). 주요 필드:

    - `document_id` — 이 요청의 고유 ID. 후속 `/documents/{id}/…` 호출에 사용.
    - `pages[]` — 페이지별 최상위 노드 목록.
      - `nodes[]` — 각 기사/광고/헤더. `headline`, `body_blocks`, `images`, `bbox`.
    - `quality.warnings[]` — 백엔드 폴백 등 비치명적 이슈 리스트.
    - `diff_report` — `diff_report=true`일 때만 채워짐.

    ### 에러
    - `400` — `.pdf` 확장자 아님, `reading_direction` 값 불량.
    - `413` — 파일 크기 초과.
    - `500 {"error":"mineru_failure",...}` — MinerU 내부 오류.
      `stderr_tail`에 마지막 1000자가 담김.
    """

    # --- size limit ---
    content = await file.read()
    size_mb = len(content) / (1024 * 1024)
    if size_mb > settings.max_upload_mb:
        raise HTTPException(
            status_code=413,
            detail=f"File too large: {size_mb:.1f}MB > {settings.max_upload_mb}MB",
        )

    filename = file.filename or "upload.pdf"
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(
            status_code=400,
            detail="Only PDF files are accepted.",
        )

    # reading_direction 유효성 검증 (form 문자열이므로 여기서 필터)
    direction = (reading_direction or "").lower().strip() or None
    if direction is not None and direction not in {"ltr", "rtl"}:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Invalid reading_direction '{reading_direction}'. "
                "Use 'ltr' (left-to-right) or 'rtl' (right-to-left)."
            ),
        )

    try:
        response = parse_pdf(
            pdf_bytes=content,
            filename=filename,
            backend=backend,
            language=language,
            mode=mode,
            diff_report=diff_report,
            reading_direction=direction,
        )
    except MineruError as e:
        logger.exception("Parse failed")
        raise HTTPException(
            status_code=500,
            detail={
                "error": "mineru_failure",
                "message": str(e),
                "stderr_tail": (e.stderr or "")[-1000:],
            },
        ) from e
    except Exception as e:
        logger.exception("Unexpected parse failure")
        raise HTTPException(status_code=500, detail=f"Internal error: {e}") from e

    return response


@app.exception_handler(HTTPException)
async def http_exception_handler(_, exc: HTTPException) -> JSONResponse:
    """Standardize HTTPException output shape."""
    detail = exc.detail
    if isinstance(detail, dict):
        return JSONResponse(status_code=exc.status_code, content=detail)
    return JSONResponse(status_code=exc.status_code, content={"detail": detail})


# ---------------------------------------------------------------------------
# Debug / inspection endpoints (v5+에서 추가됨, 변경 없음)
# ---------------------------------------------------------------------------


def _document_dir(document_id: str) -> Path:
    if "/" in document_id or ".." in document_id or "\\" in document_id:
        raise HTTPException(status_code=400, detail="Invalid document_id.")
    doc_dir = settings.output_root / document_id
    if not doc_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"Unknown document_id: {document_id}")
    return doc_dir


def _load_raw_content_list(doc_dir: Path) -> list[dict[str, Any]]:
    raw_dir = doc_dir / "raw"
    candidates = list(raw_dir.rglob("*_content_list.json")) if raw_dir.exists() else []
    if not candidates:
        raise HTTPException(
            status_code=404,
            detail=(
                "No raw content_list.json archived for this document."
            ),
        )
    with candidates[0].open(encoding="utf-8") as f:
        return json.load(f)


@app.get(
    "/documents/{document_id}/pages/{page_number}/blocks",
    tags=["debug"],
    summary="특정 페이지의 원본 MinerU 블록 덤프",
)
async def get_page_blocks(document_id: str, page_number: int) -> dict[str, Any]:
    """지정된 페이지의 **가공 전** MinerU content_list 블록을 모두 반환.

    파싱 결과가 의심스러울 때(기사가 합쳐졌다 / 누락됐다 / 광고가 기사로 분류됐다 등)
    원본 블록이 어떻게 생겼는지 직접 보고 싶을 때 사용하세요.

    ### Path 파라미터
    - `document_id` *(필수)* — `POST /parse` 응답의 `document_id` 그대로.
      보안상 `/`, `\\\\`, `..` 를 포함하면 400.
    - `page_number` *(필수)* — **1-based** 페이지 번호. 1 미만이면 400.

    ### 반환
    ```json
    {
      "document_id": "doc-abc123",
      "page_number": 1,
      "block_count": 42,
      "blocks": [ { ... raw MinerU block ... }, ... ]
    }
    ```

    ### 에러
    - `400` — document_id 형식 불량 또는 `page_number < 1`.
    - `404` — document_id 없음 또는 raw artifact 미보관.
    """
    doc_dir = _document_dir(document_id)
    if page_number < 1:
        raise HTTPException(status_code=400, detail="page_number must be >= 1")

    content_list = _load_raw_content_list(doc_dir)
    target_idx = page_number - 1
    blocks = [b for b in content_list if b.get("page_idx") == target_idx]
    return {
        "document_id": document_id,
        "page_number": page_number,
        "block_count": len(blocks),
        "blocks": blocks,
    }


@app.get(
    "/documents/{document_id}/raw",
    tags=["debug"],
    summary="문서의 MinerU raw artifact 목록",
)
async def list_raw_artifacts(document_id: str) -> dict[str, Any]:
    """이 문서에 대해 MinerU가 만든 원본 파일들 목록.

    `content_list.json`, `layout.pdf`, 디버그 이미지 등 MinerU가 뱉는 모든
    중간 산출물을 볼 수 있습니다. 각 파일은 `download_url`로 다운로드 가능.

    ### Path 파라미터
    - `document_id` *(필수)* — `POST /parse` 응답의 `document_id`.

    ### 반환
    ```json
    {
      "document_id": "doc-abc123",
      "files": [
        {
          "relative_path": "sample/auto/sample_content_list.json",
          "size_bytes": 12345,
          "download_url": "/documents/doc-abc123/raw/sample/auto/sample_content_list.json"
        }
      ]
    }
    ```

    raw artifact가 없으면 `files: []` (200 OK).
    """
    doc_dir = _document_dir(document_id)
    raw_dir = doc_dir / "raw"
    if not raw_dir.is_dir():
        return {"document_id": document_id, "files": []}
    files = []
    for path in sorted(raw_dir.rglob("*")):
        if path.is_file():
            rel = path.relative_to(raw_dir).as_posix()
            files.append(
                {
                    "relative_path": rel,
                    "size_bytes": path.stat().st_size,
                    "download_url": f"/documents/{document_id}/raw/{rel}",
                }
            )
    return {"document_id": document_id, "files": files}


@app.get(
    "/documents/{document_id}/raw/{file_path:path}",
    tags=["debug"],
    summary="MinerU raw artifact 다운로드",
)
async def download_raw_artifact(document_id: str, file_path: str) -> FileResponse:
    """MinerU raw 디렉터리 안의 개별 파일을 그대로 스트리밍 다운로드.

    ### Path 파라미터
    - `document_id` *(필수)* — `POST /parse` 응답의 `document_id`.
    - `file_path` *(필수)* — `GET /documents/{id}/raw`의 `relative_path` 값.
      `..` 로 시작하거나 절대경로면 400 (path traversal 방지).

    ### 반환
    원본 바이너리 파일 (`Content-Disposition: attachment; filename=...`).

    ### 에러
    - `400` — `file_path`가 `..` 포함 또는 `/` 로 시작.
    - `404` — 파일이 실제로 없음.
    """
    doc_dir = _document_dir(document_id)
    if ".." in file_path or file_path.startswith("/"):
        raise HTTPException(status_code=400, detail="Invalid file path.")
    target = (doc_dir / "raw" / file_path).resolve()
    raw_root = (doc_dir / "raw").resolve()
    if not str(target).startswith(str(raw_root)):
        raise HTTPException(status_code=400, detail="Path traversal rejected.")
    if not target.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {file_path}")
    return FileResponse(path=target, filename=target.name)
