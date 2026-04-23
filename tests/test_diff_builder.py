"""Tests for diff_builder.

중요: OCR이 틀렸는지 알아내는 게 목적이 아님. 두 결과가 다른지만 보고함.
"""
from __future__ import annotations

from paperslice.diff_builder import build_diff_report


def _mk_block(page: int, bbox: list[float], text: str, type_: str = "text") -> dict:
    return {"type": type_, "page_idx": page, "bbox": bbox, "text": text}


def test_identical_inputs_produce_no_diff() -> None:
    """두 실행 결과가 완전히 같으면 differences가 비어야 함."""
    blocks = [
        _mk_block(0, [10, 10, 100, 50], "三菱ケミカル"),
        _mk_block(0, [10, 60, 100, 100], "アセトンシアンヒドリン"),
    ]
    report = build_diff_report(blocks, blocks)
    assert report.total_blocks_compared == 2
    assert report.differing_blocks == 0
    assert report.differences == []


def test_simple_text_difference_caught() -> None:
    """OCR이 オタ를 낸 블록을 잡아낸다."""
    ocr = [_mk_block(0, [10, 10, 100, 50], "チイナ")]
    txt = [_mk_block(0, [10, 10, 100, 50], "チャイナ")]
    report = build_diff_report(ocr, txt)
    assert report.total_blocks_compared == 1
    assert report.differing_blocks == 1
    diff = report.differences[0]
    assert diff.page == 1
    assert diff.ocr_text == "チイナ"
    assert diff.txt_text == "チャイナ"


def test_bbox_tolerance_allows_small_drift() -> None:
    """bbox가 몇 px 다른 것은 같은 블록으로 매칭."""
    ocr = [_mk_block(0, [10.0, 10.0, 100.0, 50.0], "同じ")]
    txt = [_mk_block(0, [12.0, 11.0, 103.0, 52.0], "同じ")]  # 2~3px drift
    report = build_diff_report(ocr, txt)
    assert report.total_blocks_compared == 1
    assert report.differing_blocks == 0


def test_bbox_mismatch_excludes_block() -> None:
    """bbox가 크게 다르면 페어링 안 됨. 레이아웃 차이는 리포트 안 함."""
    ocr = [_mk_block(0, [10, 10, 100, 50], "A")]
    txt = [_mk_block(0, [500, 500, 600, 550], "B")]  # 전혀 다른 위치
    report = build_diff_report(ocr, txt)
    assert report.total_blocks_compared == 0
    assert report.differing_blocks == 0


def test_non_text_blocks_ignored() -> None:
    """image/table 블록은 비교 대상이 아님."""
    ocr = [
        _mk_block(0, [10, 10, 100, 50], "テキスト"),
        {"type": "image", "page_idx": 0, "bbox": [200, 10, 300, 100]},
    ]
    txt = [
        _mk_block(0, [10, 10, 100, 50], "テキスト"),
        {"type": "image", "page_idx": 0, "bbox": [200, 10, 300, 100]},
    ]
    report = build_diff_report(ocr, txt)
    assert report.total_blocks_compared == 1  # 텍스트만
    assert report.differing_blocks == 0


def test_multi_page_matching() -> None:
    """여러 페이지에 걸쳐 각자의 블록만 매칭."""
    ocr = [
        _mk_block(0, [10, 10, 100, 50], "p1-ocr"),
        _mk_block(1, [20, 20, 120, 60], "p2-ocr"),
    ]
    txt = [
        _mk_block(0, [10, 10, 100, 50], "p1-txt"),
        _mk_block(1, [20, 20, 120, 60], "p2-txt"),
    ]
    report = build_diff_report(ocr, txt)
    assert report.total_blocks_compared == 2
    assert report.differing_blocks == 2
    # 페이지 번호는 1-based
    pages = sorted(d.page for d in report.differences)
    assert pages == [1, 2]


def test_one_side_has_more_blocks() -> None:
    """한쪽에만 있는 블록은 리포트에 안 잡힘. 차이 기록이 아니라 페어링 기록."""
    ocr = [
        _mk_block(0, [10, 10, 100, 50], "a"),
        _mk_block(0, [200, 200, 300, 300], "b"),  # 이 블록은 txt에 없음
    ]
    txt = [
        _mk_block(0, [10, 10, 100, 50], "a"),
    ]
    report = build_diff_report(ocr, txt)
    assert report.total_blocks_compared == 1
    assert report.differing_blocks == 0


def test_empty_inputs() -> None:
    """양쪽 다 비어도 안 터짐."""
    report = build_diff_report([], [])
    assert report.total_blocks_compared == 0
    assert report.differing_blocks == 0
    assert report.differences == []
