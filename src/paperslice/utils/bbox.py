"""Bounding box utilities used by the segmenter and classifier."""
from __future__ import annotations

from ..schemas import BoundingBox


def normalize_bbox(raw: list[float] | None) -> BoundingBox | None:
    """Accept a [x0, y0, x1, y1] list and return a BoundingBox or None.

    Handles the various shapes MinerU can emit (list, None, malformed).
    """
    if not raw or len(raw) != 4:
        return None
    try:
        x0, y0, x1, y1 = (float(v) for v in raw)
    except (TypeError, ValueError):
        return None
    # Ensure x0 <= x1, y0 <= y1 even if the source got them reversed
    return BoundingBox(
        x0=min(x0, x1),
        y0=min(y0, y1),
        x1=max(x0, x1),
        y1=max(y0, y1),
    )


def bbox_union(boxes: list[BoundingBox]) -> BoundingBox | None:
    """Tightest bbox enclosing all inputs. Returns None for empty list."""
    if not boxes:
        return None
    return BoundingBox(
        x0=min(b.x0 for b in boxes),
        y0=min(b.y0 for b in boxes),
        x1=max(b.x1 for b in boxes),
        y1=max(b.y1 for b in boxes),
    )


def bbox_iou(a: BoundingBox, b: BoundingBox) -> float:
    """Intersection-over-union. 0.0 for disjoint boxes."""
    ix0 = max(a.x0, b.x0)
    iy0 = max(a.y0, b.y0)
    ix1 = min(a.x1, b.x1)
    iy1 = min(a.y1, b.y1)
    if ix0 >= ix1 or iy0 >= iy1:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    union = a.area + b.area - inter
    return inter / union if union > 0 else 0.0


def bbox_vertical_gap(a: BoundingBox, b: BoundingBox) -> float:
    """Vertical gap between two boxes. Negative if they overlap vertically."""
    if a.y1 < b.y0:
        return b.y0 - a.y1
    if b.y1 < a.y0:
        return a.y0 - b.y1
    return -1.0


def bbox_horizontal_overlap_ratio(a: BoundingBox, b: BoundingBox) -> float:
    """Fraction of horizontal overlap vs the narrower box's width.

    Used by the segmenter to decide whether two blocks belong to the same
    column. 1.0 means one box is fully within the other horizontally.
    """
    ox0 = max(a.x0, b.x0)
    ox1 = min(a.x1, b.x1)
    overlap = max(ox1 - ox0, 0.0)
    narrower = min(a.width, b.width)
    return overlap / narrower if narrower > 0 else 0.0
