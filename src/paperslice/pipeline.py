"""End-to-end orchestration: PDF bytes -> ParseResponse.

v7에서 했던 것: mode, diff_report 파라미터 추가.
v8에서 바뀌는 것:
- [1/8] ~ [8/8] 단계 로그를 순차적으로 찍음 (완료 시점 + 결과값)
- pdf_type_detector가 이제 DetectionResult 반환 (method + reason)
"""
from __future__ import annotations

import logging
import shutil
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .asset_manager import persist_images
from .block_enricher import enrich_blocks
from .classifier import classify_blocks
from .config import MineruBackend, settings
from .cpu_tuning import detect_cpu_tuning
from .diff_builder import build_diff_report
from .mineru_runner import (
    MineruError,
    MineruResult,
    get_mineru_version,
    run_mineru,
)
from .pdf_chunker import (
    PdfChunk,
    get_page_count,
    merge_chunk_outputs,
    split_pdf_into_chunks,
)
from .pdf_type_detector import detect_mineru_method
from .schemas import (
    DiffReport,
    ParseMode,
    ParseResponse,
    QualityInfo,
    SourceInfo,
)
from .segmenter import segment

logger = logging.getLogger(__name__)

# 전체 단계 수. [n/8] 포맷에 쓰임.
_TOTAL_STEPS = 8


def _run_mineru_maybe_chunked(
    pdf_path: Path,
    mineru_out: Path,
    scratch_dir: Path,
    backend: MineruBackend,
    language: str,
    method: str,
    chunk_label: str,
) -> tuple[MineruResult, int]:
    """큰 PDF 는 페이지 단위로 쪼개 MinerU 를 여러 번 호출하고 결과를 병합.

    페이지 수가 `settings.chunk_threshold_pages` 이하이거나 `chunk_pages=0`
    이면 기존처럼 1회만 호출. chunk_label 은 로그용 ('primary'/'secondary').

    반환: (병합된 MineruResult, 실행된 chunk 개수).
    """
    mineru_out.mkdir(parents=True, exist_ok=True)
    chunk_size = settings.chunk_pages
    threshold = settings.chunk_threshold_pages

    total_pages = get_page_count(pdf_path)
    should_chunk = (
        chunk_size > 0
        and total_pages > threshold
        and total_pages > chunk_size
    )

    if not should_chunk:
        result = run_mineru(
            pdf_path=pdf_path,
            output_dir=mineru_out,
            backend=backend,
            language=language,
            method=method,
        )
        return result, 1

    chunks = split_pdf_into_chunks(pdf_path, scratch_dir, chunk_size)
    if len(chunks) <= 1:
        # 분할이 실제로 일어나지 않은 edge case — 일반 경로로 폴백.
        result = run_mineru(
            pdf_path=pdf_path,
            output_dir=mineru_out,
            backend=backend,
            language=language,
            method=method,
        )
        return result, 1

    per_chunk: list[tuple[PdfChunk, list[dict], Path]] = []
    first_backend: MineruBackend | None = None
    first_method_used = ""
    for chunk in chunks:
        chunk_out = mineru_out / f"chunk{chunk.index:03d}"
        t_chunk = time.perf_counter()
        logger.info(
            "MinerU chunk %s %d/%d: pages %d-%d (%d pages)",
            chunk_label,
            chunk.index + 1,
            len(chunks),
            chunk.start_page + 1,
            chunk.end_page,
            chunk.page_count,
        )
        result = run_mineru(
            pdf_path=chunk.path,
            output_dir=chunk_out,
            backend=backend,
            language=language,
            method=method,
        )
        logger.info(
            "MinerU chunk %s %d/%d 완료: blocks=%d [%s]",
            chunk_label,
            chunk.index + 1,
            len(chunks),
            len(result.content_list),
            _fmt_dur(time.perf_counter() - t_chunk),
        )
        if first_backend is None:
            first_backend = result.backend_used
            first_method_used = result.method_used
        per_chunk.append((chunk, result.content_list, result.raw_output_dir))

    merged = merge_chunk_outputs(per_chunk, mineru_out / "merged")
    return (
        MineruResult(
            content_list=merged.content_list,
            raw_output_dir=merged.raw_output_dir,
            backend_used=first_backend or backend,
            method_used=first_method_used,
        ),
        len(chunks),
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fmt_dur(seconds: float) -> str:
    """경과 시간 포맷. 60초 미만은 '12.3s', 이상은 '7m23s'."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(int(seconds), 60)
    return f"{m}m{s:02d}s"


def _log_step(num: int, name: str, result: str) -> None:
    """[n/8] {name} 완료 → {result} 형식으로 INFO 로그 출력."""
    logger.info("[%d/%d] %s 완료 → %s", num, _TOTAL_STEPS, name, result)


def _save_raw_output(result: MineruResult, document_output_dir: Path, suffix: str = "") -> None:
    """MinerU raw artifacts를 document output dir에 보존.

    suffix: 파일명 구분용 ('' 본 실행, '_diff' 보조 실행).
    """
    raw_dir = document_output_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    for path in result.raw_output_dir.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(result.raw_output_dir)
        dest_name = rel.name
        if suffix:
            stem, ext = dest_name.rsplit(".", 1) if "." in dest_name else (dest_name, "")
            dest_name = f"{stem}{suffix}.{ext}" if ext else f"{stem}{suffix}"
        dest = raw_dir / rel.parent / dest_name
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(path, dest)
        except Exception as e:
            logger.warning("Failed to archive raw artifact %s: %s", path, e)


def _resolve_method_v8(mode: ParseMode, pdf_path: Path) -> tuple[str, str]:
    """ParseMode → (method, reason_for_log).

    mode가 auto일 때만 detector 돌리고, ocr/txt 명시이면 그대로 통과.
    """
    if mode is ParseMode.ocr:
        return ("ocr", "mode=ocr 명시")
    if mode is ParseMode.txt:
        return ("txt", "mode=txt 명시")
    # auto
    det = detect_mineru_method(pdf_path)
    return (det.method, f"auto 판별 → {det.reason}")


def parse_pdf(
    pdf_bytes: bytes,
    filename: str,
    backend: MineruBackend | None = None,
    language: str | None = None,
    document_id: str | None = None,
    mode: ParseMode | None = None,
    diff_report: bool = False,
    reading_direction: str | None = None,
) -> ParseResponse:
    """Run the full parse pipeline on a PDF payload.

    ``reading_direction`` controls column ordering during segmentation.
    ``"rtl"`` flips the column order for traditional Japanese/Chinese
    newspapers (read right-to-left). ``"ltr"`` or ``None`` keeps the
    default left-to-right order suitable for Korean/Western layouts.
    """
    backend = backend or settings.default_backend
    language = language or settings.default_language
    mode = mode or ParseMode.auto
    document_id = document_id or f"doc-{uuid.uuid4().hex[:12]}"
    direction = (reading_direction or "ltr").lower()

    logger.info(
        "Starting parse: document_id=%s filename=%s backend=%s language=%s mode=%s diff_report=%s reading_direction=%s",
        document_id, filename, backend, language, mode.value, diff_report, direction,
    )

    t_total = time.perf_counter()

    document_output_dir = settings.output_root / document_id
    document_output_dir.mkdir(parents=True, exist_ok=True)

    settings.scratch_root.mkdir(parents=True, exist_ok=True)
    scratch_dir = Path(tempfile.mkdtemp(prefix="parse-", dir=settings.scratch_root))

    warnings: list[str] = []

    try:
        # PDF bytes를 디스크에 저장 (MinerU는 파일 경로만 받음)
        pdf_path = scratch_dir / filename
        pdf_path.write_bytes(pdf_bytes)

        # ================================================================
        # [1/8] PDF 타입 감지
        # ================================================================
        t_step = time.perf_counter()
        primary_method, detect_reason = _resolve_method_v8(mode, pdf_path)
        _log_step(
            1,
            "PDF 타입 감지",
            f"method={primary_method} ({detect_reason}) [{_fmt_dur(time.perf_counter() - t_step)}]",
        )

        # ================================================================
        # [2/8] MinerU 실행 (primary)
        # ================================================================
        t_step = time.perf_counter()
        mineru_out = scratch_dir / "mineru-out"
        # CPU 튜닝 요약 1줄 — 운영 중 OOM 원인 추적용.
        _tune = detect_cpu_tuning()
        logger.info(
            "[2/8] MinerU 시작 → threads=%d (source=%s), vram_gb=%d, "
            "device=%s, formula=%s, chunk_pages=%d",
            _tune.threads,
            _tune.source,
            settings.mineru_virtual_vram_gb,
            settings.mineru_device_mode,
            settings.mineru_formula_enable,
            settings.chunk_pages,
        )
        try:
            result, primary_chunks = _run_mineru_maybe_chunked(
                pdf_path=pdf_path,
                mineru_out=mineru_out,
                scratch_dir=scratch_dir,
                backend=backend,
                language=language,
                method=primary_method,
                chunk_label="primary",
            )
        except MineruError as e:
            logger.error("MinerU failed (primary): %s\nstderr: %s", e, e.stderr[:2000])
            raise

        if result.backend_used != backend:
            warnings.append(
                f"Requested backend '{backend.value}' unavailable; "
                f"used '{result.backend_used.value}' instead."
            )
        backend_note = (
            result.backend_used.value
            if result.backend_used == backend
            else f"{result.backend_used.value} (요청:{backend.value})"
        )
        chunk_note = f", chunks={primary_chunks}" if primary_chunks > 1 else ""
        _log_step(
            2,
            "MinerU 실행",
            (
                f"method={primary_method}, backend={backend_note}, "
                f"content_list={len(result.content_list)} blocks{chunk_note} "
                f"[{_fmt_dur(time.perf_counter() - t_step)}]"
            ),
        )

        # ================================================================
        # [3/8] Diff 보조 실행 (옵션)
        # ================================================================
        t_step = time.perf_counter()
        diff_report_obj: DiffReport | None = None
        if not diff_report:
            _log_step(3, "Diff 보조 실행", "생략 (diff_report=false)")
        elif result.backend_used is not MineruBackend.pipeline:
            warnings.append(
                f"diff_report was requested but backend is "
                f"'{result.backend_used.value}' which doesn't support "
                f"method selection. Skipping diff."
            )
            _log_step(
                3,
                "Diff 보조 실행",
                f"생략 (backend={result.backend_used.value}는 method 선택 미지원)",
            )
        else:
            secondary_method = "txt" if primary_method == "ocr" else "ocr"
            secondary_out = scratch_dir / "mineru-out-diff"
            try:
                secondary, _secondary_chunks = _run_mineru_maybe_chunked(
                    pdf_path=pdf_path,
                    mineru_out=secondary_out,
                    scratch_dir=scratch_dir,
                    backend=backend,
                    language=language,
                    method=secondary_method,
                    chunk_label="secondary",
                )
                if primary_method == "ocr":
                    ocr_cl, txt_cl = result.content_list, secondary.content_list
                else:
                    ocr_cl, txt_cl = secondary.content_list, result.content_list
                diff_report_obj = build_diff_report(ocr_cl, txt_cl)
                _save_raw_output(secondary, document_output_dir, suffix="_diff")
                _log_step(
                    3,
                    "Diff 보조 실행",
                    (
                        f"secondary method={secondary_method}, "
                        f"differing={diff_report_obj.differing_blocks}/"
                        f"{diff_report_obj.total_blocks_compared} "
                        f"[{_fmt_dur(time.perf_counter() - t_step)}]"
                    ),
                )
            except MineruError as e:
                logger.warning(
                    "Secondary MinerU run for diff failed: %s. Continuing without diff.",
                    e,
                )
                warnings.append(
                    f"diff_report requested but secondary run failed: {e}"
                )
                _log_step(3, "Diff 보조 실행", f"실패, diff 없이 진행 ({e})")

        # ================================================================
        # [4/8] 블록 Enrich
        # ================================================================
        t_step = time.perf_counter()
        enriched = enrich_blocks(result.content_list)
        _log_step(
            4,
            "블록 Enrich",
            f"{len(enriched)} blocks [{_fmt_dur(time.perf_counter() - t_step)}]",
        )

        # ================================================================
        # [5/8] 이미지 저장
        # ================================================================
        t_step = time.perf_counter()
        asset_paths = persist_images(
            blocks=enriched,
            raw_output_dir=result.raw_output_dir,
            document_output_dir=document_output_dir,
        )
        _log_step(
            5,
            "이미지 저장",
            f"{len(asset_paths)} images [{_fmt_dur(time.perf_counter() - t_step)}]",
        )

        # ================================================================
        # [6/8] 블록 분류
        # ================================================================
        t_step = time.perf_counter()
        classified = classify_blocks(enriched)
        _log_step(
            6,
            "블록 분류",
            f"{len(classified)} blocks [{_fmt_dur(time.perf_counter() - t_step)}]",
        )

        # ================================================================
        # [7/8] 세그먼트
        # ================================================================
        t_step = time.perf_counter()
        pages = segment(classified, asset_paths, reading_direction=direction)
        total_nodes = sum(len(p.nodes) for p in pages)
        _log_step(
            7,
            "세그먼트",
            (
                f"pages={len(pages)}, nodes={total_nodes}, direction={direction} "
                f"[{_fmt_dur(time.perf_counter() - t_step)}]"
            ),
        )

        # raw MinerU artifacts 보존 (로그 없이 background)
        _save_raw_output(result, document_output_dir)

        # ================================================================
        # [8/8] 응답 조립
        # ================================================================
        t_step = time.perf_counter()
        page_count = max((p.page_number for p in pages), default=0)
        mode_used_str = result.method_used or mode.value
        response = ParseResponse(
            document_id=document_id,
            source=SourceInfo(
                filename=filename,
                page_count=page_count,
                parser_backend=result.backend_used.value,
                parser_version=get_mineru_version(),
                parsed_at=_now_iso(),
                mode_used=mode_used_str,
            ),
            pages=pages,
            quality=QualityInfo(
                status="success",
                warnings=warnings,
            ),
            assets_dir=document_id,
            diff_report=diff_report_obj,
        )
        (document_output_dir / "result.json").write_text(
            response.model_dump_json(indent=2),
            encoding="utf-8",
        )
        _log_step(
            8,
            "응답 조립",
            (
                f"document_id={document_id}, page_count={page_count}, "
                f"warnings={len(warnings)} [총 {_fmt_dur(time.perf_counter() - t_total)}]"
            ),
        )

        return response

    finally:
        shutil.rmtree(scratch_dir, ignore_errors=True)
