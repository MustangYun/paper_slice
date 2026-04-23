"""PDF 를 작은 페이지 묶음으로 쪼개는 유틸.

v9에서 추가: MinerU 는 한 번 호출될 때 문서 전체(최대 `window_size=64` 페이지)를
한꺼번에 배치 추론한다. CPU 에서 이 피크가 메모리를 터뜨리므로, 미리 PDF 를
N 페이지 단위 서브 PDF 로 잘라 MinerU 를 N 번 호출한다. 각 호출의 피크 메모리는
chunk 크기에 비례하고, 결과 content_list 는 page_idx 를 오프셋해서 합친다.

PyMuPDF 에 의존 — pyproject.toml 기본 의존성에 이미 포함.
"""
from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# MinerU content_list 블록에서 이미지 경로가 들어 있는 후보 필드명.
# block_enricher 가 이미 둘 다 읽고 있으므로 여기서도 동일하게 취급.
_IMAGE_PATH_FIELDS: tuple[str, ...] = ("img_path", "image_path", "path")


@dataclass(frozen=True)
class PdfChunk:
    """잘라낸 서브 PDF 한 조각.

    path: 스크래치 디렉터리에 쓰여진 서브 PDF 경로
    start_page: 원본 PDF 기준 시작 페이지 (0-based, inclusive)
    end_page: 원본 PDF 기준 끝 페이지 (0-based, exclusive)
    index: 전체 chunk 중 0-based 순번 (로그용)
    """

    path: Path
    start_page: int
    end_page: int
    index: int

    @property
    def page_count(self) -> int:
        return self.end_page - self.start_page


def get_page_count(pdf_path: Path) -> int:
    """PyMuPDF 로 페이지 수 확인. 실패 시 -1."""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return -1
    try:
        doc = fitz.open(str(pdf_path))
    except Exception as e:
        logger.warning("페이지 수 계산 실패 (%s): %s", pdf_path.name, e)
        return -1
    try:
        return len(doc)
    finally:
        doc.close()


def split_pdf_into_chunks(
    pdf_path: Path,
    scratch_dir: Path,
    chunk_size: int,
) -> list[PdfChunk]:
    """PDF 를 `chunk_size` 페이지씩 잘라 scratch_dir/chunks/ 아래에 서브 PDF 생성.

    반환: PdfChunk 리스트 (최소 1개). 분할할 필요가 없거나 실패하면 원본을
    감싼 1개짜리 리스트를 돌려준다 — 호출부가 chunk 루프 분기 없이 동일하게
    처리할 수 있도록.
    """
    if chunk_size <= 0:
        return [_whole_document_chunk(pdf_path)]

    try:
        import fitz  # PyMuPDF
    except ImportError:
        logger.warning("PyMuPDF 없음 — chunk 분할 불가, 원본 그대로 처리")
        return [_whole_document_chunk(pdf_path)]

    try:
        src = fitz.open(str(pdf_path))
    except Exception as e:
        logger.warning("PDF 열기 실패 (%s) — 원본 그대로 처리", e)
        return [_whole_document_chunk(pdf_path)]

    total_pages = len(src)
    if total_pages <= chunk_size:
        src.close()
        return [_whole_document_chunk(pdf_path)]

    out_dir = scratch_dir / "chunks"
    out_dir.mkdir(parents=True, exist_ok=True)

    chunks: list[PdfChunk] = []
    stem = pdf_path.stem
    try:
        for idx, start in enumerate(range(0, total_pages, chunk_size)):
            end = min(start + chunk_size, total_pages)
            sub = fitz.open()  # empty
            try:
                # from_page/to_page 는 inclusive.
                sub.insert_pdf(src, from_page=start, to_page=end - 1)
                # MinerU 는 파일명을 그대로 출력 디렉터리 이름으로 삼기 때문에
                # 인덱스를 접두어로 붙여 각 chunk 를 구분.
                chunk_path = out_dir / f"{stem}__chunk{idx:03d}_p{start + 1}-{end}.pdf"
                sub.save(str(chunk_path))
            finally:
                sub.close()
            chunks.append(
                PdfChunk(
                    path=chunk_path,
                    start_page=start,
                    end_page=end,
                    index=idx,
                )
            )
    finally:
        src.close()

    logger.info(
        "PDF chunked: %d pages → %d chunks of ~%d pages (%s)",
        total_pages,
        len(chunks),
        chunk_size,
        pdf_path.name,
    )
    return chunks


def _whole_document_chunk(pdf_path: Path) -> PdfChunk:
    """분할 없이 통째 처리할 때 쓰는 단일 chunk.

    end_page 는 get_page_count 결과로 최대한 채우되, 실패해도 동작은 유지.
    page_idx 오프셋이 0 이라 content_list 를 그대로 사용 가능.
    """
    n = get_page_count(pdf_path)
    return PdfChunk(
        path=pdf_path,
        start_page=0,
        end_page=max(1, n) if n > 0 else 1,
        index=0,
    )


def merge_content_lists(
    per_chunk: list[tuple[PdfChunk, list[dict]]],
) -> list[dict]:
    """chunk 별 MinerU content_list 를 합치면서 page_idx 를 원본 기준으로 오프셋.

    각 block 의 `page_idx` 는 해당 chunk 안에서 0-based. 원본 PDF 페이지 번호로
    바꾸려면 chunk.start_page 를 더한다. 원본 block dict 는 건드리지 않고
    얕은 복사로 새 리스트를 만든다.
    """
    merged: list[dict] = []
    for chunk, content in per_chunk:
        offset = chunk.start_page
        for block in content:
            new_block = dict(block)
            if isinstance(block.get("page_idx"), int):
                new_block["page_idx"] = block["page_idx"] + offset
            merged.append(new_block)
    return merged


@dataclass(frozen=True)
class MergedChunkOutput:
    """`merge_chunk_outputs` 의 반환값.

    content_list: page_idx 오프셋 및 image_path 재작성이 끝난 최종 리스트
    raw_output_dir: 이미지까지 복사 완료된 가상 MinerU 출력 디렉터리
    """

    content_list: list[dict]
    raw_output_dir: Path


def merge_chunk_outputs(
    per_chunk: list[tuple[PdfChunk, list[dict], Path]],
    merged_dir: Path,
) -> MergedChunkOutput:
    """chunk 결과들을 하나의 "가상 MinerU 출력 디렉터리" 로 통합.

    각 튜플은 (chunk, 해당 chunk 의 raw content_list, 해당 chunk 의 raw_output_dir).
    - 이미지는 `merged_dir/images/c<idx>_<원래이름>` 형태로 복사.
    - 이미지 경로 필드는 위 새 상대 경로로 재작성.
    - `page_idx` 는 chunk.start_page 만큼 오프셋.
    - 최종 content_list 는 `merged_dir/merged_content_list.json` 에도 기록해
      debug 용 raw artifact 에 그대로 실리게 한다.
    """
    images_dir = merged_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    merged: list[dict] = []
    copied = 0
    for chunk, content, raw_dir in per_chunk:
        offset = chunk.start_page
        for block in content:
            new_block = dict(block)
            if isinstance(block.get("page_idx"), int):
                new_block["page_idx"] = block["page_idx"] + offset

            for field in _IMAGE_PATH_FIELDS:
                orig = block.get(field)
                if not isinstance(orig, str) or not orig:
                    continue
                src = raw_dir / orig
                if not src.exists():
                    # MinerU 가 참조는 남겼지만 실제로 못 뽑은 경우 — 경로만 둔다.
                    continue
                new_name = f"c{chunk.index:03d}_{Path(orig).name}"
                dst_rel = f"images/{new_name}"
                dst = merged_dir / dst_rel
                try:
                    shutil.copy2(src, dst)
                    copied += 1
                except OSError as e:
                    logger.warning("chunk 이미지 복사 실패 %s → %s: %s", src, dst, e)
                    continue
                new_block[field] = dst_rel
            merged.append(new_block)

    # debug 용: 병합 결과를 JSON 으로도 남김. raw 보존 단계에서 그대로 복사됨.
    try:
        out_path = merged_dir / "merged_content_list.json"
        out_path.write_text(
            json.dumps(merged, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as e:
        logger.warning("merged_content_list.json 쓰기 실패: %s", e)

    logger.info(
        "chunk 결과 병합: chunks=%d, blocks=%d, images_copied=%d",
        len(per_chunk),
        len(merged),
        copied,
    )
    return MergedChunkOutput(content_list=merged, raw_output_dir=merged_dir)
