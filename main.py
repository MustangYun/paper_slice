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
        "일본어 신문 PDF를 기사/광고/헤더 단위로 분리. "
        "page-accurate provenance, 이미지 자산 보존 지원."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}


@app.get("/info")
async def info() -> dict[str, object]:
    """Runtime metadata."""
    return {
        "paperslice_version": __version__,
        "mineru_version": get_mineru_version(),
        "default_backend": settings.default_backend.value,
        "gpu_available": _gpu_available(),
        "strict_gpu": settings.strict_gpu,
        "output_root": str(settings.output_root),
    }


@app.post("/parse", response_model=ParseResponse)
async def parse(
    file: Annotated[UploadFile, File(description="PDF 파일 (필수).")],
    backend: Annotated[
        MineruBackend | None,
        Form(description=(
            "MinerU 백엔드. pipeline(기본) | vlm | hybrid. "
            "vlm/hybrid는 GPU 필요."
        )),
    ] = None,
    language: Annotated[
        str | None,
        Form(description="OCR 언어 코드 (기본: japan)."),
    ] = None,
    mode: Annotated[
        ParseMode | None,
        Form(description=(
            "파싱 방식 (pipeline 백엔드에서만 의미 있음). "
            "auto(기본): PDF 타입 자동 판별 / "
            "ocr: 강제 OCR (스캔 PDF 용) / "
            "txt: 강제 텍스트 레이어 추출 (디지털 PDF 용, 더 정확)"
        )),
    ] = None,
    diff_report: Annotated[
        bool,
        Form(description=(
            "true면 ocr과 txt 두 방식을 모두 실행하고 차이 리포트를 응답에 포함. "
            "시간이 2배로 걸리니 필요할 때만 켜세요. 기본: false."
        )),
    ] = False,
    reading_direction: Annotated[
        str | None,
        Form(description=(
            "기사 column을 읽는 방향. 일본·중국·대만의 전통 신문은 "
            "세로쓰기 + 오른쪽→왼쪽으로 읽으므로 `rtl`을 고르세요. "
            "한국·서양 신문은 `ltr` (기본값).\n\n"
            "- `ltr` (left-to-right): 왼쪽 column부터 순서대로 읽음.\n"
            "- `rtl` (right-to-left): 오른쪽 column부터 읽음. 일본 신문에 권장.\n\n"
            "이 설정은 기사 순서와 가로 헤드라인 ↔ 본문 column 매칭 "
            "정확도에 영향을 줍니다. 한 column 안의 내용 자체는 동일합니다."
        )),
    ] = None,
) -> ParseResponse:
    """PDF를 업로드해서 구조화된 기사 데이터로 반환."""

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


@app.get("/documents/{document_id}/pages/{page_number}/blocks")
async def get_page_blocks(document_id: str, page_number: int) -> dict[str, Any]:
    """Return every raw MinerU block on a specific page."""
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


@app.get("/documents/{document_id}/raw")
async def list_raw_artifacts(document_id: str) -> dict[str, Any]:
    """List raw MinerU artifacts archived for this document."""
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


@app.get("/documents/{document_id}/raw/{file_path:path}")
async def download_raw_artifact(document_id: str, file_path: str) -> FileResponse:
    """Download a single raw artifact."""
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
