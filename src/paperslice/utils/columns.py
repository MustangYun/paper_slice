"""Column detection for multi-column newspaper layouts.

Japanese newspapers are typically laid out in 4-8 vertical columns. MinerU
returns blocks in roughly y-order across the whole page, which mixes blocks
from different columns together. That's fine for prose but catastrophic for
article segmentation: a headline in column 1 ends up attached to body text
from column 3.

This module clusters block bboxes by their horizontal position (x-center)
into "columns", then provides a reading order (column-by-column, top-to-
bottom within a column) that matches how a human reads the page.

The algorithm is intentionally simple — 1D clustering with a gap threshold.
It handles the common cases (4-column tech newspaper, 6-column daily) without
requiring ML. For unusual layouts (overlapping columns, magazine-style free
layout) the fallback is to treat the whole page as one column, which gives
the same behavior as the previous segmenter.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..classifier import ClassifiedBlock
from ..schemas import BoundingBox

logger = logging.getLogger(__name__)


@dataclass
class Column:
    """A vertical strip of the page containing blocks that share an x-range."""

    index: int  # left-to-right column number, 0-based
    x_min: float  # leftmost x0 of any block in this column
    x_max: float  # rightmost x1 of any block in this column
    blocks: list[ClassifiedBlock] = field(default_factory=list)

    @property
    def x_center(self) -> float:
        return (self.x_min + self.x_max) / 2

    @property
    def width(self) -> float:
        return self.x_max - self.x_min


# Tuning knobs. These defaults worked well on the test PDF (chemical
# industry daily, 4-column layout). If you need to tune for a different
# publication, expose these via `detect_columns(..., min_gap=...)`.
_DEFAULT_MIN_GAP_RATIO = 0.03  # gap must be >= 3% of page width to split
_DEFAULT_MIN_BLOCKS_PER_COLUMN = 2  # merge singleton "columns" into neighbors
_WIDE_BLOCK_THRESHOLD = 0.8  # block spanning >80% of page width = full-page
_SPANNING_MULTIPLIER = 2.5  # a block this much wider than median = spanning


def _estimate_page_width(blocks: list[ClassifiedBlock]) -> float:
    """Use the rightmost x1 seen as a proxy for page width."""
    xs = [cb.block.bbox.x1 for cb in blocks if cb.block.bbox]
    return max(xs) if xs else 1000.0


def _is_full_width(
    bbox: BoundingBox, page_width: float, median_block_width: float
) -> bool:
    """Does this block span multiple columns, making it impossible to
    assign to any single column?

    Two independent triggers:
    1. The block covers >=50% of page width (clearly a banner/full page).
    2. The block is >=2.5x wider than the median block (e.g., a headline
       that spans 2-3 narrow columns on a newspaper page).

    The median-based trigger is the important one for Japanese newspapers:
    a headline spanning 3 columns isn't 50% of page width, but it still
    can't belong to any single column.
    """
    if bbox.width >= _WIDE_BLOCK_THRESHOLD * page_width:
        return True
    # Median-based detection for multi-column headlines.
    if median_block_width > 0 and bbox.width >= _SPANNING_MULTIPLIER * median_block_width:
        return True
    return False


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2 == 1:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2


def detect_columns(
    blocks: list[ClassifiedBlock],
    min_gap_ratio: float = _DEFAULT_MIN_GAP_RATIO,
) -> list[Column]:
    """Cluster blocks into vertical columns by x-center.

    Returns columns sorted left-to-right. Blocks with no bbox, or that
    span multiple columns (full-page banners, multi-column headlines),
    go into a "spanning" column (index=-1) which callers handle
    separately.

    Algorithm:
    1. Pull out spanning/no-bbox blocks first.
    2. Sort remaining blocks by x-center.
    3. Walk the sorted list; start a new column whenever the gap between
       consecutive x-centers exceeds `min_gap_ratio * page_width`.

    This is deliberately NOT "merge overlapping x-ranges": that approach
    produces a chain-reaction where each block extends the running
    x-range, until every block appears to "overlap" and the page
    collapses into one column. x-center gap is robust because narrow
    column-internal blocks cluster tightly, while inter-column gaps are
    wide by construction.
    """
    if not blocks:
        return []

    page_width = _estimate_page_width(blocks)
    min_gap = min_gap_ratio * page_width

    # Median block width anchors the "spanning" detection — a banner has
    # to be both wide in absolute terms and wider than typical blocks.
    widths = [cb.block.bbox.width for cb in blocks if cb.block.bbox]
    median_block_width = _median(widths)

    # Separate spanning / no-bbox blocks first; they get their own bucket.
    spanning: list[ClassifiedBlock] = []
    positioned: list[ClassifiedBlock] = []
    for cb in blocks:
        if cb.block.bbox is None:
            spanning.append(cb)
        elif _is_full_width(cb.block.bbox, page_width, median_block_width):
            spanning.append(cb)
        else:
            positioned.append(cb)

    if not positioned:
        # Entire page is spanning/no-bbox — one synthetic column.
        return [Column(index=-1, x_min=0.0, x_max=page_width, blocks=spanning)]

    # Sort by x-center to find gaps.
    positioned.sort(key=lambda cb: (cb.block.bbox.x0 + cb.block.bbox.x1) / 2)

    columns: list[Column] = []
    current_blocks: list[ClassifiedBlock] = []
    current_x_min = positioned[0].block.bbox.x0
    current_x_max = positioned[0].block.bbox.x1
    prev_center = (positioned[0].block.bbox.x0 + positioned[0].block.bbox.x1) / 2

    for cb in positioned:
        bbox = cb.block.bbox
        center = (bbox.x0 + bbox.x1) / 2
        gap = center - prev_center
        # New column when the x-center jumps by more than the threshold.
        # Pure gap-based — no overlap check, to avoid chain-reaction merging.
        if current_blocks and gap > min_gap:
            columns.append(
                Column(
                    index=len(columns),
                    x_min=current_x_min,
                    x_max=current_x_max,
                    blocks=current_blocks,
                )
            )
            current_blocks = []
            current_x_min = bbox.x0
            current_x_max = bbox.x1

        current_blocks.append(cb)
        current_x_min = min(current_x_min, bbox.x0)
        current_x_max = max(current_x_max, bbox.x1)
        prev_center = center

    # Flush the last column
    if current_blocks:
        columns.append(
            Column(
                index=len(columns),
                x_min=current_x_min,
                x_max=current_x_max,
                blocks=current_blocks,
            )
        )

    # Merge tiny columns (1 block) into the nearest neighbor. A 1-block
    # "column" is usually noise (a floating caption, a page number) and
    # splitting on it fragments real columns.
    columns = _merge_small_columns(columns)

    # Re-index after merging so indices stay 0..N-1 left-to-right.
    for i, col in enumerate(columns):
        col.index = i

    # Append the spanning bucket (if any) with index=-1 so the segmenter
    # knows these blocks don't live in any single column.
    if spanning:
        columns.append(Column(index=-1, x_min=0.0, x_max=page_width, blocks=spanning))

    logger.debug(
        "detect_columns: %d columns on page (widths: %s, spanning: %d)",
        len([c for c in columns if c.index != -1]),
        [f"{c.x_min:.0f}-{c.x_max:.0f}" for c in columns if c.index != -1],
        len(spanning),
    )
    return columns


def _merge_small_columns(columns: list[Column]) -> list[Column]:
    """Merge 1-block columns into the nearest neighbor by x-center distance."""
    if len(columns) <= 1:
        return columns

    result: list[Column] = []
    for col in columns:
        if len(col.blocks) >= _DEFAULT_MIN_BLOCKS_PER_COLUMN or not result:
            result.append(col)
            continue

        # Find nearest existing column to merge into
        nearest = min(
            result,
            key=lambda c: abs(c.x_center - col.x_center),
        )
        # Also compare against the next column if any
        # (handled naturally by the loop continuing)
        nearest.blocks.extend(col.blocks)
        nearest.x_min = min(nearest.x_min, col.x_min)
        nearest.x_max = max(nearest.x_max, col.x_max)

    return result


def reading_order(columns: list[Column]) -> list[ClassifiedBlock]:
    """Flatten columns into a single reading-order sequence.

    For Japanese newspapers, which are read right-to-left, you might want
    `reversed(columns[:-1])` instead. For now we go left-to-right because
    MinerU's OCR output is already in left-to-right text order, and the
    segmenter only cares about blocks-within-column grouping, not global
    reading direction.

    Within each column, blocks are sorted top-to-bottom by y0. The spanning
    bucket (index=-1) comes last and keeps its original order.
    """
    ordered: list[ClassifiedBlock] = []
    for col in columns:
        if col.index == -1:
            # Spanning blocks at the end, in their original order.
            ordered.extend(col.blocks)
            continue
        sorted_blocks = sorted(
            col.blocks,
            key=lambda cb: (cb.block.bbox.y0 if cb.block.bbox else 0.0),
        )
        ordered.extend(sorted_blocks)
    return ordered


def reassign_spanning_headlines(columns: list[Column]) -> list[Column]:
    """Move headline blocks from the spanning bucket into the regular
    column they most likely introduce.

    Japanese newspapers often use wide headlines that visually span 2-3
    narrow body columns, but the article body underneath flows down just
    ONE of those columns. detect_columns conservatively places such
    headlines in the spanning bucket (because they don't fit in any
    single column), but the segmenter then has no way to attach body
    text from a regular column to them.

    This helper picks, for each spanning headline, the column that:
      (a) starts below the headline (y0 >= headline.y0), and
      (b) has the largest horizontal overlap with the headline's bbox.
    and inserts the headline at the top of that column's block list.

    Only headlines are reassigned. Full-page ad banners, page headers,
    and other spanning blocks stay in the spanning bucket.
    """
    from ..classifier import BlockRole  # local import to avoid cycles

    spanning = next((c for c in columns if c.index == -1), None)
    if spanning is None or not spanning.blocks:
        return columns

    regular = [c for c in columns if c.index != -1]
    if not regular:
        return columns

    kept_in_spanning: list[ClassifiedBlock] = []
    for cb in spanning.blocks:
        bbox = cb.block.bbox
        # Only reassign headlines with a valid bbox.
        if bbox is None or cb.role != BlockRole.headline:
            kept_in_spanning.append(cb)
            continue

        # Score each regular column by x-overlap with the headline,
        # constrained to columns whose body starts at or below the
        # headline (since a headline introduces what's beneath it).
        best_col: Column | None = None
        best_overlap = 0.0
        for col in regular:
            # The column must have at least one block starting below the
            # headline — otherwise the headline has nothing to "introduce".
            has_body_below = any(
                cb2.block.bbox and cb2.block.bbox.y0 >= bbox.y0
                for cb2 in col.blocks
            )
            if not has_body_below:
                continue
            overlap = max(
                0.0,
                min(bbox.x1, col.x_max) - max(bbox.x0, col.x_min),
            )
            if overlap > best_overlap:
                best_overlap = overlap
                best_col = col

        if best_col is None or best_overlap <= 0.0:
            kept_in_spanning.append(cb)
            continue

        # Insert the headline at the position matching its y-order.
        best_col.blocks.append(cb)
        # Expand the column's x-range to include the headline bbox so
        # downstream logic that might inspect column geometry sees the
        # true extent.
        best_col.x_min = min(best_col.x_min, bbox.x0)
        best_col.x_max = max(best_col.x_max, bbox.x1)

    spanning.blocks = kept_in_spanning
    # Drop empty spanning bucket so downstream code sees a clean picture.
    if not spanning.blocks:
        return regular
    return regular + [spanning]
