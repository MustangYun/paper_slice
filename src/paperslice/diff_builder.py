"""OCR vs txt 비교 리포트 생성.

pipeline.py가 `diff_report=true`일 때 MinerU를 두 번 돌린다:
- 한 번은 선택된 mode로 (결과는 실제 응답에 씀)
- 한 번은 반대 mode로 (비교용)

이 모듈은 그 두 content_list를 받아 BlockDiff / DiffReport를 만든다.

매칭 전략:
- 같은 (page_idx, bbox 근사일치) 블록을 페어링
- bbox는 실수 비교이므로 5px 허용오차
- 페어링된 블록들의 text 필드가 다르면 BlockDiff 생성
- 페어링 안 된 블록은 리포트 안 나감 (레이아웃 차이는 별개 문제)

주의: "어느 쪽이 맞다"는 판단 안 함. 단순 diff 로그.
"""
from __future__ import annotations

import logging
from typing import Any

from .schemas import BlockDiff, BoundingBox, DiffReport

logger = logging.getLogger(__name__)

# bbox 좌표 매칭 허용 오차 (px). MinerU 두 경로가 같은 블록이라도
# 몇 픽셀 차이가 날 수 있음.
_BBOX_TOLERANCE = 5.0


def _is_text_block(b: dict[str, Any]) -> bool:
    """content_list 원소가 텍스트 블록인지 판별."""
    return b.get("type") == "text"


def _extract_text(b: dict[str, Any]) -> str:
    """content_list 원소에서 텍스트 추출. 비어있으면 빈 문자열."""
    # MinerU content_list는 'text' 필드에 평문 저장
    return (b.get("text") or "").strip()


def _bbox_match(b1: list[float] | None, b2: list[float] | None) -> bool:
    """두 bbox가 사실상 같은 영역인지 판별 (5px 허용)."""
    if not b1 or not b2:
        return False
    if len(b1) != 4 or len(b2) != 4:
        return False
    return all(abs(b1[i] - b2[i]) <= _BBOX_TOLERANCE for i in range(4))


def _to_bbox_model(bbox: list[float] | None) -> BoundingBox | None:
    """list[float] → BoundingBox. 깨진 값이면 None."""
    if not bbox or len(bbox) != 4:
        return None
    try:
        return BoundingBox(x0=bbox[0], y0=bbox[1], x1=bbox[2], y1=bbox[3])
    except Exception:
        return None


def build_diff_report(
    ocr_content_list: list[dict[str, Any]],
    txt_content_list: list[dict[str, Any]],
) -> DiffReport:
    """두 content_list를 페어링해서 차이나는 텍스트 블록만 기록.

    Args:
        ocr_content_list: -m ocr 로 실행한 MinerU의 content_list
        txt_content_list: -m txt 로 실행한 MinerU의 content_list
    Returns:
        DiffReport with differing_blocks populated.
    """
    ocr_text_blocks = [b for b in ocr_content_list if _is_text_block(b)]
    txt_text_blocks = [b for b in txt_content_list if _is_text_block(b)]

    # 페이지별로 나눠서 매칭 (전체 리스트 O(N²)을 피함)
    by_page_ocr: dict[int, list[dict[str, Any]]] = {}
    for b in ocr_text_blocks:
        by_page_ocr.setdefault(b.get("page_idx", -1), []).append(b)
    by_page_txt: dict[int, list[dict[str, Any]]] = {}
    for b in txt_text_blocks:
        by_page_txt.setdefault(b.get("page_idx", -1), []).append(b)

    differences: list[BlockDiff] = []
    matched = 0

    # 페이지 단위로 순회하면서 bbox 매칭된 블록 비교
    for page_idx, ocr_blocks in by_page_ocr.items():
        txt_blocks = by_page_txt.get(page_idx, [])
        if not txt_blocks:
            continue

        used_txt_idxs: set[int] = set()
        for ob in ocr_blocks:
            ob_bbox = ob.get("bbox")
            # 같은 페이지의 아직 사용 안 된 txt 블록 중에서 bbox 일치 탐색
            matched_tb = None
            matched_idx = -1
            for i, tb in enumerate(txt_blocks):
                if i in used_txt_idxs:
                    continue
                if _bbox_match(ob_bbox, tb.get("bbox")):
                    matched_tb = tb
                    matched_idx = i
                    break
            if matched_tb is None:
                continue
            used_txt_idxs.add(matched_idx)
            matched += 1

            ocr_text = _extract_text(ob)
            txt_text = _extract_text(matched_tb)

            if ocr_text != txt_text:
                differences.append(
                    BlockDiff(
                        page=page_idx + 1,  # content_list는 0-based
                        bbox=_to_bbox_model(ob_bbox),
                        ocr_text=ocr_text,
                        txt_text=txt_text,
                    )
                )

    logger.info(
        "Diff report: matched %d blocks across pages, %d differ",
        matched, len(differences),
    )

    return DiffReport(
        total_blocks_compared=matched,
        differing_blocks=len(differences),
        differences=differences,
    )
