"""PDF 타입을 판별해서 MinerU에 어떤 method를 쓸지 결정한다.

v8 변경점:
- 세로쓰기(일본·중국 전통 레이아웃) 감지 추가
- 디지털 PDF여도 세로쓰기면 `ocr` 반환 (MinerU의 `-m txt`가
  세로쓰기에서 문자를 빠뜨리는 치명적 버그 우회)
- 판별 이유를 `DetectionResult.reason`으로 반환해 파이프라인이 로그에 씀

판별 순서:
1) PyMuPDF로 PDF 열기 실패 → ocr (fallback)
2) 평균 텍스트 밀도 < 500자/페이지 → 스캔본 → ocr
3) 세로쓰기 비율 >= 30% → 일본·중국 신문 류 → ocr
4) 위 조건 다 통과 → 가로쓰기 디지털 → txt
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# 페이지당 평균 이 정도 이상이면 디지털 PDF로 본다.
_TEXT_DENSITY_THRESHOLD = 500

# 판별할 때 몇 페이지까지만 볼지 (큰 PDF에서 전체 긁으면 느림)
_SAMPLE_PAGES = 3

# 세로쓰기 감지 지표: 평균 line 당 글자 수.
# - 가로쓰기 정상 문서: 한 line에 10-80자 (단어·문장 단위)
# - 세로쓰기 일본 신문: 각 한자가 별도 line으로 분리되어 1-2자/line
# PyMuPDF는 세로쓰기 PDF에서도 dir=(1,0)으로 표기하므로 dir 방향으로는
# 감지 불가. line 길이가 훨씬 안정적인 지표.
_MIN_CHARS_PER_LINE_HORIZONTAL = 5.0


@dataclass(frozen=True)
class DetectionResult:
    """detect_mineru_method()의 반환 타입.

    method: 'ocr' | 'txt' — MinerU -m 값
    reason: 사람이 읽을 수 있는 판별 근거 (로그용)
    """
    method: str
    reason: str


def detect_mineru_method(pdf_path: Path) -> DetectionResult:
    """PDF 내용을 보고 MinerU method와 그 판별 이유를 반환."""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return DetectionResult("ocr", "PyMuPDF 없음 (fallback)")

    try:
        doc = fitz.open(str(pdf_path))
    except Exception as e:
        return DetectionResult("ocr", f"PDF 열기 실패 ({e})")

    try:
        n_pages = len(doc)
        if n_pages == 0:
            return DetectionResult("ocr", "0 페이지")

        sample_count = min(_SAMPLE_PAGES, n_pages)

        # --- 2단계: 텍스트 밀도 ---
        total_chars = 0
        for i in range(sample_count):
            total_chars += len(doc[i].get_text())
        avg_chars = total_chars / sample_count

        if avg_chars < _TEXT_DENSITY_THRESHOLD:
            return DetectionResult(
                "ocr",
                f"평균 {avg_chars:.0f}자/페이지 < {_TEXT_DENSITY_THRESHOLD} (스캔본)",
            )

        # --- 3단계: 세로쓰기 감지 (v8 신규) ---
        # 일본 신문 PDF는 각 한자가 별도 line으로 배치된다.
        # (PyMuPDF는 글자가 회전 안 돼있어서 dir=(1,0)으로 보고하므로
        # dir 방향으로는 감지 불가. 대신 line당 글자 수로 판정.)
        total_lines = 0
        total_line_chars = 0
        for i in range(sample_count):
            try:
                raw = doc[i].get_text("dict")
            except Exception:
                continue
            for block in raw.get("blocks", []):
                for line in block.get("lines", []):
                    line_chars = sum(len(s.get("text", "")) for s in line.get("spans", []))
                    if line_chars > 0:
                        total_lines += 1
                        total_line_chars += line_chars

        avg_chars_per_line = (total_line_chars / total_lines) if total_lines > 0 else 0.0

        # total_lines == 0 이면 dict 추출이 전부 실패한 케이스. 세로쓰기 판단 불가.
        # 텍스트 밀도는 이미 충분하다고 통과했으니 txt로 진행.
        if total_lines > 0 and avg_chars_per_line < _MIN_CHARS_PER_LINE_HORIZONTAL:
            return DetectionResult(
                "ocr",
                (
                    f"평균 {avg_chars:.0f}자/페이지, "
                    f"{avg_chars_per_line:.1f}자/line < {_MIN_CHARS_PER_LINE_HORIZONTAL} "
                    f"(세로쓰기 의심 — 일본·중국 신문류)"
                ),
            )

        # --- 4단계: 가로쓰기 디지털 ---
        line_info = (
            f"{avg_chars_per_line:.1f}자/line"
            if total_lines > 0
            else "line 분석 실패"
        )
        return DetectionResult(
            "txt",
            f"평균 {avg_chars:.0f}자/페이지, {line_info} (디지털 가로쓰기)",
        )
    finally:
        doc.close()
