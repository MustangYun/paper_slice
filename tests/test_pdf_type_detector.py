"""Tests for pdf_type_detector (v8).

v8에서 DetectionResult(method, reason) 반환으로 바뀌었고
세로쓰기 감지 로직이 추가됨. 테스트도 그에 맞춰 확장.

실제 PDF 파일은 필요 없음 — fitz를 monkey-patch해서 로직만 검증.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from paperslice.pdf_type_detector import DetectionResult, detect_mineru_method


def _fake_page(plain_text: str, dict_text: dict | None = None) -> MagicMock:
    """한 페이지의 get_text('text') / get_text('dict') 응답을 모킹."""
    page = MagicMock()

    def _get_text(fmt: str = "text"):
        if fmt == "dict":
            return dict_text or {"blocks": []}
        return plain_text

    page.get_text.side_effect = _get_text
    return page


def _fake_fitz_doc(pages: list[MagicMock]) -> MagicMock:
    doc = MagicMock()
    doc.__len__.return_value = len(pages)
    doc.__getitem__.side_effect = lambda i: pages[i]
    doc.close = MagicMock()
    return doc


def _install_fake_fitz(doc: MagicMock) -> dict:
    """fitz 모듈 자체를 모킹해 sys.modules에 꽂는 patch 인자."""
    fake_fitz = MagicMock()
    fake_fitz.open.return_value = doc
    return {"fitz": fake_fitz}


def _horizontal_dict(text: str) -> dict:
    """가로쓰기: text 전체가 한 line에 들어감 (정상 문서)."""
    return {
        "blocks": [
            {
                "lines": [
                    {"dir": (1.0, 0.0), "spans": [{"text": text}]},
                ],
            },
        ],
    }


def _vertical_dict(text: str) -> dict:
    """세로쓰기: 일본 신문처럼 각 글자가 별도 line으로 분리.

    PyMuPDF는 회전 안 된 세로 배치 글자를 dir=(1,0)으로 보고하므로
    dir이 아닌 line 당 글자수(=1)로 세로쓰기를 시뮬레이션.
    """
    lines = [
        {"dir": (1.0, 0.0), "spans": [{"text": ch}]}
        for ch in text
    ]
    return {"blocks": [{"lines": lines}]}


def _mixed_dict_chars_per_line(horiz_text: str, vert_text: str) -> dict:
    """가로쓰기 한 덩어리 + 세로쓰기(글자당 1 line) 섞인 페이지."""
    lines = [{"dir": (1.0, 0.0), "spans": [{"text": horiz_text}]}]
    lines.extend(
        {"dir": (1.0, 0.0), "spans": [{"text": ch}]}
        for ch in vert_text
    )
    return {"blocks": [{"lines": lines}]}


# ---------------------------------------------------------------------------
# 기존 v7 케이스 (DetectionResult 반환으로 수정)
# ---------------------------------------------------------------------------

def test_digital_horizontal_pdf_returns_txt() -> None:
    """가로쓰기 + 텍스트 밀도 충분 → txt."""
    text = "あ" * 10000
    pages = [_fake_page(text, _horizontal_dict(text)) for _ in range(3)]
    doc = _fake_fitz_doc(pages)
    with patch.dict(sys.modules, _install_fake_fitz(doc)):
        result = detect_mineru_method(Path("/nonexistent.pdf"))
    assert isinstance(result, DetectionResult)
    assert result.method == "txt"
    assert "가로쓰기" in result.reason or "세로쓰기" in result.reason  # reason 존재 확인


def test_scanned_pdf_returns_ocr() -> None:
    """텍스트 거의 없는 스캔 PDF → ocr."""
    pages = [_fake_page("", _horizontal_dict("")) for _ in range(3)]
    doc = _fake_fitz_doc(pages)
    with patch.dict(sys.modules, _install_fake_fitz(doc)):
        result = detect_mineru_method(Path("/nonexistent.pdf"))
    assert result.method == "ocr"
    assert "스캔본" in result.reason


def test_below_threshold_returns_ocr() -> None:
    """밀도 threshold 밑은 ocr."""
    text = "あ" * 100
    pages = [_fake_page(text, _horizontal_dict(text)) for _ in range(2)]
    doc = _fake_fitz_doc(pages)
    with patch.dict(sys.modules, _install_fake_fitz(doc)):
        result = detect_mineru_method(Path("/nonexistent.pdf"))
    assert result.method == "ocr"


def test_pymupdf_unavailable_falls_back_to_ocr() -> None:
    """fitz import 실패 시 안전하게 ocr."""
    real_import = (
        __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__
    )

    def fake_import(name, *args, **kwargs):
        if name == "fitz":
            raise ImportError("No module named 'fitz'")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=fake_import):
        sys.modules.pop("fitz", None)
        result = detect_mineru_method(Path("/nonexistent.pdf"))
    assert result.method == "ocr"
    assert "PyMuPDF" in result.reason


def test_broken_pdf_returns_ocr() -> None:
    """fitz.open() 실패 시 ocr."""
    fake_fitz = MagicMock()
    fake_fitz.open.side_effect = RuntimeError("broken PDF")
    with patch.dict(sys.modules, {"fitz": fake_fitz}):
        result = detect_mineru_method(Path("/nonexistent.pdf"))
    assert result.method == "ocr"
    assert "열기 실패" in result.reason


def test_empty_pdf_returns_ocr() -> None:
    """0페이지 PDF → ocr."""
    doc = _fake_fitz_doc([])
    with patch.dict(sys.modules, _install_fake_fitz(doc)):
        result = detect_mineru_method(Path("/nonexistent.pdf"))
    assert result.method == "ocr"


# ---------------------------------------------------------------------------
# v8 신규: 세로쓰기 감지 케이스
# ---------------------------------------------------------------------------

def test_vertical_japanese_newspaper_returns_ocr() -> None:
    """일본 신문(글자당 1 line) → ocr 강제.

    실제 화학공업일보에서 PyMuPDF가 이런 구조로 읽어온다:
    4000+ line, 평균 ~2자/line, dir은 여전히 (1,0).
    """
    text = "あ" * 10000  # 10000 line이 될 것, 각 line 1자
    pages = [_fake_page(text, _vertical_dict(text)) for _ in range(3)]
    doc = _fake_fitz_doc(pages)
    with patch.dict(sys.modules, _install_fake_fitz(doc)):
        result = detect_mineru_method(Path("/nonexistent.pdf"))
    assert result.method == "ocr"
    assert "세로쓰기" in result.reason
    assert "자/line" in result.reason


def test_mixed_layout_still_low_chars_per_line_returns_ocr() -> None:
    """가로 덩어리 조금 + 세로 글자별 line 많이 → 평균 낮아서 ocr."""
    # 가로쓰기 100자 1 line + 세로쓰기 9900자 9900 line
    # 평균: 10000 / 9901 ≈ 1.01 자/line → ocr
    horiz = "い" * 100
    vert = "あ" * 9900
    page_dict = _mixed_dict_chars_per_line(horiz, vert)
    pages = [_fake_page(horiz + vert, page_dict) for _ in range(3)]
    doc = _fake_fitz_doc(pages)
    with patch.dict(sys.modules, _install_fake_fitz(doc)):
        result = detect_mineru_method(Path("/nonexistent.pdf"))
    assert result.method == "ocr"
    assert "세로쓰기" in result.reason


def test_mostly_horizontal_with_short_lines_returns_txt() -> None:
    """가로쓰기 여러 line (평균 line 길이 충분) → txt.

    한국어 논문 같은 경우 한 문단이 여러 line으로 쪼개지지만,
    한 line에 10+자는 들어가므로 threshold(5) 넘어 txt.
    """
    # 한 line에 30자씩 100개 line = 3000자
    page_dict = {
        "blocks": [
            {
                "lines": [
                    {"dir": (1.0, 0.0), "spans": [{"text": "あ" * 30}]}
                    for _ in range(100)
                ],
            },
        ],
    }
    text = "あ" * 3000
    pages = [_fake_page(text, page_dict) for _ in range(3)]
    doc = _fake_fitz_doc(pages)
    with patch.dict(sys.modules, _install_fake_fitz(doc)):
        result = detect_mineru_method(Path("/nonexistent.pdf"))
    assert result.method == "txt"
    assert "가로쓰기" in result.reason


def test_vertical_detection_fails_gracefully() -> None:
    """get_text('dict')가 예외 던져도 통과 (밀도 기준으로만 판정)."""
    page = MagicMock()

    def _get_text(fmt: str = "text"):
        if fmt == "dict":
            raise RuntimeError("dict extraction failed")
        return "あ" * 10000

    page.get_text.side_effect = _get_text
    doc = _fake_fitz_doc([page, page, page])
    with patch.dict(sys.modules, _install_fake_fitz(doc)):
        result = detect_mineru_method(Path("/nonexistent.pdf"))
    # dict 실패하면 total_lines=0 → 세로쓰기 판단 보류 → 밀도 충분하니 txt
    assert result.method == "txt"


def test_detection_result_is_frozen() -> None:
    """DetectionResult는 불변이어야 함."""
    import dataclasses
    result = DetectionResult("ocr", "test")
    assert result.method == "ocr"
    assert result.reason == "test"
    # dataclasses 모듈이 frozen=True 메타데이터를 보유하는지 확인
    assert result.__dataclass_params__.frozen is True
