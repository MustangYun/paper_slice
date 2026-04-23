"""Human-readable location descriptions for bboxes.

Converts a bounding box into phrases like "1페이지 우측 상단" so the
result.json is more skimmable when a human is verifying parses without
a visual viewer.

Design:
- Page is divided into a 3x3 grid (3 horizontal × 3 vertical bands).
- The bbox's center determines which cell it falls into.
- Page width/height are inferred from the document's max observed x1/y1
  per page (rasterization-time values aren't always in MinerU output).

Why 3x3: finer grids (5x5) produce descriptions that are harder to hold
in your head ("상단에서 두 번째 행의 중앙") without actually helping
locate the block. 3x3 matches the intuitive "상/중/하 × 좌/중/우" that
most Korean speakers use when describing newspaper layouts.
"""
from __future__ import annotations

from ..schemas import BoundingBox

# Horizontal bands (left-to-right) and vertical bands (top-to-bottom).
# These are expressed in Korean because result.json is primarily consumed
# by Korean-speaking reviewers for this project.
_H_LABELS = ("좌측", "중앙", "우측")
_V_LABELS = ("상단", "중단", "하단")


def describe_location(
    bbox: BoundingBox | None,
    page: int,
    page_width: float,
    page_height: float,
) -> str | None:
    """Render a human-readable page-region phrase for a bbox.

    Returns None if the bbox is missing or the page dimensions are
    unknown (can't position the bbox without them).

    Examples:
        (page=1, bbox centered top-left)    -> "1페이지 좌측 상단"
        (page=3, bbox centered middle)       -> "3페이지 중앙 중단"
        (page=5, bbox centered bottom-right) -> "5페이지 우측 하단"
    """
    if bbox is None:
        return None
    if page_width <= 0 or page_height <= 0:
        return None

    cx = (bbox.x0 + bbox.x1) / 2
    cy = (bbox.y0 + bbox.y1) / 2

    # Map center into 3x3 grid. Clamp to [0, 2] so out-of-page bboxes
    # (which do happen with OCR noise) still produce sensible labels.
    col = min(2, max(0, int(cx / (page_width / 3))))
    row = min(2, max(0, int(cy / (page_height / 3))))

    return f"{page}페이지 {_H_LABELS[col]} {_V_LABELS[row]}"


def infer_page_dimensions(
    bboxes: list[BoundingBox],
) -> tuple[float, float]:
    """Estimate page width/height from observed bboxes.

    MinerU doesn't always include page dimensions in its output, so we
    use the maximum x1/y1 across all blocks on a page as a proxy. This
    slightly underestimates the true page size (blocks usually don't
    touch page edges) but the location grid is coarse enough that a
    5-10% underestimate doesn't change the grid cell.
    """
    if not bboxes:
        return 0.0, 0.0
    max_x = max(b.x1 for b in bboxes)
    max_y = max(b.y1 for b in bboxes)
    return max_x, max_y
