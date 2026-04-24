"""Group classified blocks into article / ad / header nodes per page.

Strategy:
1. Split blocks by page.
2. On each page, cluster blocks into vertical columns (see utils/columns).
   Multi-column newspaper layouts are segmented per-column; a headline in
   column 1 never pulls body text from column 3.
3. Within each column, walk blocks in top-to-bottom order. Headlines open
   new articles; body/image/table blocks attach to the current article in
   the same column.
4. Full-width / banner blocks (spanning >50% of page width) are processed
   after columns and never pull cross-column content.
5. Ads and headers/footers are segregated into their own nodes regardless
   of column.
6. Orphan body blocks (no preceding headline in the same column) form an
   '(untitled)' article with reduced confidence.

This is meaningfully better than the previous single-pass segmenter:
- Articles no longer bleed across columns.
- Ads are segregated into their own nodes.
- Headers/footers don't pollute articles.
- Every node is column-scoped, so confidence scores reflect real grouping
  quality rather than MinerU's reading-order artifacts.

Future improvements (left as hooks):
- LLM_HOOK for low-confidence orphan blocks — ask an LLM which neighboring
  article each orphan belongs to. See classifier.py.
- Spatial caption matching: promote a short body block right next to an
  image to that image's caption.
- Right-to-left reading order for traditional Japanese layouts (currently
  left-to-right; doesn't affect per-article correctness, only inter-
  article ordering).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from .block_enricher import EnrichedBlock
from .classifier import BlockRole, ClassifiedBlock
from .schemas import (
    ArticleNode,
    BoundingBox,
    ImageNode,
    NodeKind,
    PageNode,
    Provenance,
    TextNode,
)
from .utils.bbox import bbox_union
from .utils.location import describe_location, infer_page_dimensions

logger = logging.getLogger(__name__)


# Short aliases for kind IDs in node_ids.
_KIND_ALIAS = {
    NodeKind.article: "art",
    NodeKind.advertisement: "ad",
    NodeKind.header: "hdr",
    NodeKind.unknown: "unk",
}


@dataclass
class _ArticleBuilder:
    """Mutable scratch state while accumulating blocks into an article."""

    kind: NodeKind
    headline: TextNode | None = None
    body_blocks: list[TextNode] = None
    images: list[ImageNode] = None
    bboxes: list[BoundingBox] = None
    confidence: float = 1.0
    block_ids: list[str] = None

    def __post_init__(self) -> None:
        self.body_blocks = self.body_blocks or []
        self.images = self.images or []
        self.bboxes = self.bboxes or []
        self.block_ids = self.block_ids or []

    def to_node(self, node_id: str) -> ArticleNode:
        return ArticleNode(
            node_id=node_id,
            kind=self.kind,
            confidence=self.confidence,
            headline=self.headline,
            body_blocks=self.body_blocks,
            images=self.images,
            bbox=bbox_union(self.bboxes),
        )


def _provenance(block: EnrichedBlock) -> Provenance:
    return Provenance(
        page=block.page,
        bbox=block.bbox,
        block_ids=[block.block_id],
    )


def _make_text_node(block: EnrichedBlock) -> TextNode:
    return TextNode(
        text=block.text.replace("\n", " ").strip(),
        provenance=_provenance(block),
    )


def _make_image_node(
    block: EnrichedBlock,
    image_id: str,
    stored_path: str,
    caption_text: str | None = None,
) -> ImageNode:
    caption = None
    if caption_text:
        caption = TextNode(
            text=caption_text,
            # Caption shares the image's provenance when MinerU didn't
            # give it its own bbox.
            provenance=_provenance(block),
        )
    return ImageNode(
        image_id=image_id,
        stored_path=stored_path,
        provenance=_provenance(block),
        caption=caption,
    )


def _group_by_page(
    classified: list[ClassifiedBlock],
) -> dict[int, list[ClassifiedBlock]]:
    pages: dict[int, list[ClassifiedBlock]] = {}
    for cb in classified:
        pages.setdefault(cb.block.page, []).append(cb)
    return pages


def segment_page(
    page_num: int,
    page_blocks: list[ClassifiedBlock],
    asset_paths: dict[str, tuple[str, str]],  # block_id -> (image_id, stored_path)
    reading_direction: str = "ltr",
) -> PageNode:
    """Turn one page's classified blocks into a PageNode with typed nodes.

    The page is first split into vertical columns. Each column is
    segmented independently, so a headline in column 1 never pulls body
    text from column 3. Full-width / spanning blocks are processed after
    all columns are done, so they don't break up column-internal flow.

    ``reading_direction``:
      - ``"ltr"`` (default): process columns left-to-right. Suitable for
        Korean and Western newspapers.
      - ``"rtl"``: process columns right-to-left. Traditional Japanese
        newspapers are read this way, and reversing the traversal order
        gives articles in the order a Japanese reader would expect them.
        It does NOT change what's inside a column, only the sequence in
        which whole columns are read — which matters for article-to-
        article ordering and for the spanning-headline reassignment step.

    `asset_paths` maps image-block ids to (image_id, stored_path) as
    assigned by the asset_manager. It's passed in so the segmenter stays
    free of filesystem concerns.
    """
    # Lazy import avoids a package-level circular dependency when
    # utils/columns imports from classifier.
    from .utils.columns import detect_columns, reassign_spanning_headlines

    columns = detect_columns(page_blocks)
    # Wide headlines that span multiple narrow columns live in the
    # spanning bucket by default, but the article body they introduce
    # sits in one of the regular columns. Move each such headline into
    # the column it most visually overlaps so the segmenter can attach
    # that column's body blocks to it.
    columns = reassign_spanning_headlines(columns)

    all_builders: list[_ArticleBuilder] = []
    # Separate regular columns from the "spanning" bucket (index=-1).
    regular_columns = [c for c in columns if c.index != -1]
    spanning_bucket = next((c for c in columns if c.index == -1), None)

    # For right-to-left layouts (Japanese), traverse columns in reverse
    # order so the resulting nodes[] list reads the way a human would.
    if reading_direction == "rtl":
        regular_columns = list(reversed(regular_columns))

    # Process each column independently. Blocks inside a column are
    # sorted top-to-bottom so the "most recent article" heuristic inside
    # _process_column matches visual flow within the column.
    for col in regular_columns:
        sorted_blocks = sorted(
            col.blocks,
            key=lambda cb: (cb.block.bbox.y0 if cb.block.bbox else 0.0),
        )
        col_builders = _process_column(sorted_blocks, asset_paths)
        all_builders.extend(col_builders)

    # Process spanning blocks last. These are typically banner headlines,
    # full-page ads, or page-header strips — items that shouldn't steal
    # body text from any single column.
    if spanning_bucket and spanning_bucket.blocks:
        spanning_builders = _process_column(spanning_bucket.blocks, asset_paths)
        all_builders.extend(spanning_builders)

    # Post-process: merge orphan bodies into adjacent empty-body
    # headlines. Column-aware segmentation often leaves a headline in
    # column A (because text_level=1 got picked up) while its body text
    # sits in column B (the "wrap-around" column). Neither half is
    # useful alone; merging them recovers the original article.
    all_builders = _merge_orphans_with_empty_headlines(all_builders)

    # Compute page dimensions once for the whole page so every node's
    # location is measured against the same grid.
    page_width, page_height = _infer_page_size(all_builders)

    # Assign stable node_ids, numbered per (page, kind).
    kind_counters: dict[NodeKind, int] = {k: 0 for k in NodeKind}
    nodes: list[ArticleNode] = []
    for builder in all_builders:
        kind_counters[builder.kind] += 1
        node_id = (
            f"p{page_num}-{_KIND_ALIAS[builder.kind]}-{kind_counters[builder.kind]:02d}"
        )
        node = builder.to_node(node_id)
        # Attach human-readable location. Done here (not in _ArticleBuilder)
        # because location requires page-level dimensions that the
        # builder doesn't know about.
        if node.bbox is not None:
            node = node.model_copy(
                update={
                    "location": describe_location(
                        node.bbox, page_num, page_width, page_height
                    )
                }
            )
        nodes.append(node)

    return PageNode(page_number=page_num, nodes=nodes)


def _infer_page_size(
    builders: list[_ArticleBuilder],
) -> tuple[float, float]:
    """Find max x1/y1 across all builder bboxes to estimate page size."""
    all_bboxes: list[BoundingBox] = []
    for b in builders:
        all_bboxes.extend(b.bboxes)
    return infer_page_dimensions(all_bboxes)


# Max vertical gap (in the same units as bboxes — typically points) between
# a headline's bottom and an orphan's top for them to be considered
# mergeable. 300 points ≈ 4 inches, generous enough to cover newspaper
# layouts where body text flows past an image or spans the full column
# height, but tight enough to reject unrelated articles on different
# parts of the page.
_MERGE_MAX_Y_GAP = 300.0

# Max horizontal distance between a headline and an orphan's bboxes when
# they DON'T overlap (adjacent columns). Expressed as a fraction of the
# headline's width. Newspaper columns typically sit with a small gutter,
# so allowing up to ~1× headline-width of horizontal separation catches
# the "headline in column A, body in column B" case without letting a
# column-3 orphan merge into a column-1 headline.
_MERGE_MAX_X_DISTANCE_RATIO = 1.0

# When the bboxes DO overlap horizontally, we still want meaningful
# overlap (not just touching at the edge). 15% of the smaller bbox's
# width is enough to confirm they sit in the same vertical stripe.
_MERGE_MIN_X_OVERLAP_RATIO = 0.15

# --- Spatial matching (v2) constants ---
# A headline article is considered "weak" — and therefore still a
# candidate for absorbing more body text — when it has very few body
# blocks. This lets us recover the case where the initial column-aware
# pass found one or two paragraphs under the headline but missed the
# rest because they sit in an adjacent column.
_MERGE_WEAK_BODY_MAX = 2

# Maximum number of orphans that can merge into a single headline.
# Newspaper articles with 3+ column-wide bodies exist (front-page
# features), but allowing unlimited merging is how you end up with one
# giant "article" that ate half the page. 4 is a sane ceiling.
_MERGE_MAX_ORPHANS_PER_HEADLINE = 4

# Score weights. Lower score = better match. These are multiplicative
# weights over normalized [0, 1] sub-scores, so their relative
# magnitudes are what matters, not the absolute numbers.
_W_Y_DISTANCE = 0.40
_W_X_GAP = 0.30
_W_COL_ADJACENCY = 0.20
_W_SIZE_RATIO = 0.10

# An orphan can match a headline only if the combined score is below
# this threshold. Calibrated so that "same-or-adjacent column, small
# vertical gap, similar width" passes and "far column, huge gap" fails.
# A score of exactly 0.5 corresponds to "completely non-adjacent column
# but zero vertical gap" (col_adjacency=1.0 × 0.2 + x_gap=1.0 × 0.3 = 0.5),
# which we want to reject — so the threshold sits below that.
_MERGE_SCORE_THRESHOLD = 0.45

# Confidence decay. Each additional orphan merged into the same
# headline lowers confidence by this step, floored at 0.45 so a
# heavily-merged article is still plausibly correct but flagged as
# "please verify".
_MERGE_CONFIDENCE_START = 0.75
_MERGE_CONFIDENCE_STEP = 0.10
_MERGE_CONFIDENCE_FLOOR = 0.45


def _score_headline_orphan(
    h_bbox: BoundingBox, o_bbox: BoundingBox
) -> float | None:
    """Return a spatial-match score (lower = better), or None if the
    pairing is structurally impossible.

    Components (all normalized to [0, 1] before weighting):
      - y_distance: how far below the headline the orphan starts, as
        fraction of _MERGE_MAX_Y_GAP.
      - x_gap: horizontal gap between the bboxes, normalized by the
        headline's width. Bboxes that overlap score 0.
      - column_adjacency: 0 if same x-stripe (overlap), 0.5 if adjacent
        (within one headline-width), 1.0 if farther. Coarse but robust.
      - size_ratio: how different the bboxes' widths are. A tiny orphan
        that can't possibly be a body paragraph for a wide headline
        shouldn't win over a width-matched candidate nearby.

    Returns None for hard disqualifications: orphan above headline,
    vertical gap beyond _MERGE_MAX_Y_GAP, or degenerate widths.
    """
    # Orphan must start at or below the headline's top. A body that
    # starts above its own headline is almost certainly a different
    # article.
    if o_bbox.y0 < h_bbox.y0:
        return None

    # Vertical gap from headline bottom to orphan top. Negative gap
    # (they overlap vertically) is fine — common when the headline is
    # in the same column as the body and they touch.
    gap = o_bbox.y0 - h_bbox.y1
    if gap > _MERGE_MAX_Y_GAP:
        return None

    h_width = max(h_bbox.width, 1.0)
    o_width = max(o_bbox.width, 1.0)

    # y-distance normalized so gap==_MERGE_MAX_Y_GAP scores 1.0.
    y_score = max(0.0, gap) / _MERGE_MAX_Y_GAP

    # x-gap normalized by headline width. Negative gap (overlap) is
    # clamped to 0 — a thumbnail of overlap is as good as any overlap.
    x_distance = max(
        0.0,
        max(h_bbox.x0, o_bbox.x0) - min(h_bbox.x1, o_bbox.x1),
    )
    x_gap_score = min(1.0, x_distance / h_width)

    # Column adjacency: coarse 3-tier judgment. Overlap meaningfully =
    # same column. Close but no overlap = adjacent. Far = different.
    overlap = max(
        0.0, min(h_bbox.x1, o_bbox.x1) - max(h_bbox.x0, o_bbox.x0)
    )
    min_width = min(h_bbox.width, o_bbox.width)
    if min_width <= 0:
        return None
    overlap_ratio = overlap / min_width

    if overlap_ratio >= _MERGE_MIN_X_OVERLAP_RATIO:
        col_score = 0.0  # same column
    elif x_distance <= h_width * _MERGE_MAX_X_DISTANCE_RATIO:
        col_score = 0.5  # adjacent column
    else:
        # Not same, not adjacent. Allow it through the scorer but penalize
        # heavily — if everything else is perfect it might still win, but
        # usually won't pass the threshold.
        col_score = 1.0

    # Size ratio: 0 when widths match, 1 when they differ by 2x or more.
    width_ratio = min(h_width, o_width) / max(h_width, o_width)
    size_score = 1.0 - width_ratio  # 0 = identical, -> 1 as widths diverge

    return (
        _W_Y_DISTANCE * y_score
        + _W_X_GAP * x_gap_score
        + _W_COL_ADJACENCY * col_score
        + _W_SIZE_RATIO * size_score
    )


def _merge_orphans_with_empty_headlines(
    builders: list[_ArticleBuilder],
) -> list[_ArticleBuilder]:
    """Merge orphan bodies into nearby headlines using a spatial score.

    Column-aware segmentation routinely produces two failure modes that,
    together, lose an entire article:

      1. A headline article with zero or very few body blocks because
         the body text got placed in a different column.
      2. An orphan article (headline None, body non-empty) that is, in
         fact, the continuation of some nearby headline.

    This pass walks every headline-bearing article and, for each one,
    greedily absorbs the best-scoring orphans (see `_score_headline_orphan`
    for the score definition) until either:
      - no remaining orphan scores below `_MERGE_SCORE_THRESHOLD`, or
      - the headline has already absorbed `_MERGE_MAX_ORPHANS_PER_HEADLINE`
        orphans.

    Orphan reuse is prevented: each orphan can merge into at most one
    headline, and the "best" headline for an orphan is decided by having
    headlines pick in order — empty headlines first (they need bodies
    most), then weak headlines (1-2 body blocks).

    Confidence decays as a headline accumulates more orphans, so a
    heavily-merged article can be flagged for manual review.
    """
    # Classify candidates. Order matters: empty headlines get first pick
    # at orphans, weak headlines get second pick. That way a headline
    # with zero body doesn't lose out to one that already has some.
    empty_headlines: list[int] = []
    weak_headlines: list[int] = []
    orphans: list[int] = []
    for i, b in enumerate(builders):
        if b.kind != NodeKind.article:
            continue
        if b.headline is not None and not b.body_blocks and not b.images:
            empty_headlines.append(i)
        elif (
            b.headline is not None
            and 0 < len(b.body_blocks) <= _MERGE_WEAK_BODY_MAX
            and not b.images
        ):
            weak_headlines.append(i)
        elif b.headline is None and b.body_blocks:
            orphans.append(i)

    if not orphans or not (empty_headlines or weak_headlines):
        return builders

    used_orphans: set[int] = set()
    # Track how many orphans merged into each headline so confidence
    # decays correctly when we apply them at the end.
    merges_per_headline: dict[int, list[int]] = {}

    def _greedy_match(headline_indices: list[int]) -> None:
        """For each headline in order, absorb up to N best-scoring
        still-available orphans. Mutates `used_orphans` and
        `merges_per_headline`."""
        for h_idx in headline_indices:
            h_bbox = bbox_union(builders[h_idx].bboxes)
            if h_bbox is None:
                continue

            # Score every still-available orphan against this headline.
            scored: list[tuple[float, int]] = []
            for o_idx in orphans:
                if o_idx in used_orphans:
                    continue
                o_bbox = bbox_union(builders[o_idx].bboxes)
                if o_bbox is None:
                    continue
                score = _score_headline_orphan(h_bbox, o_bbox)
                if score is None:
                    continue
                if score > _MERGE_SCORE_THRESHOLD:
                    continue
                scored.append((score, o_idx))

            # Greedy: take the best-scoring orphans up to the cap.
            scored.sort(key=lambda pair: pair[0])
            for _score, o_idx in scored[:_MERGE_MAX_ORPHANS_PER_HEADLINE]:
                merges_per_headline.setdefault(h_idx, []).append(o_idx)
                used_orphans.add(o_idx)

    # Empty headlines first — they're the most broken.
    _greedy_match(empty_headlines)
    # Then weak headlines — they might still have missing body parts.
    _greedy_match(weak_headlines)

    if not merges_per_headline:
        return builders

    # Apply merges. Orphan body/images/bboxes flow into the headline.
    # Confidence decays linearly with the number of orphans absorbed.
    for h_idx, o_indices in merges_per_headline.items():
        h = builders[h_idx]
        for o_idx in o_indices:
            o = builders[o_idx]
            h.body_blocks.extend(o.body_blocks)
            h.images.extend(o.images)
            h.bboxes.extend(o.bboxes)
            h.block_ids.extend(o.block_ids)
        # Decay: 1 orphan -> 0.75, 2 orphans -> 0.65, 3 -> 0.55, 4 -> 0.45.
        decayed = _MERGE_CONFIDENCE_START - (
            len(o_indices) - 1
        ) * _MERGE_CONFIDENCE_STEP
        h.confidence = max(_MERGE_CONFIDENCE_FLOOR, decayed)
        logger.debug(
            "Merged %d orphan(s) into headline '%s' -> conf=%.2f",
            len(o_indices),
            h.headline.text[:40] if h.headline else "?",
            h.confidence,
        )

    return [b for i, b in enumerate(builders) if i not in used_orphans]


def _process_column(
    col_blocks: list[ClassifiedBlock],
    asset_paths: dict[str, tuple[str, str]],
) -> list[_ArticleBuilder]:
    """Run the headline-as-anchor segmentation on a single column's blocks.

    Assumes `col_blocks` is already in reading order (top-to-bottom for
    a newspaper column). Returns one _ArticleBuilder per detected article
    / ad / header node in this column.

    This is the previous single-pass logic, extracted so it can run
    independently per column.
    """
    articles: list[_ArticleBuilder] = []
    current: _ArticleBuilder | None = None

    def new_builder(kind: NodeKind) -> _ArticleBuilder:
        return _ArticleBuilder(kind=kind)

    def finalize(builder: _ArticleBuilder | None) -> None:
        if builder is None:
            return
        # Drop completely empty builders.
        if not (builder.headline or builder.body_blocks or builder.images):
            return
        articles.append(builder)

    for cb in col_blocks:
        block = cb.block
        role = cb.role

        # ---- index/TOC blocks are dropped from article flow ----
        # They'd otherwise glue themselves onto whichever article came
        # before them, contaminating the body with "see page 2, 4, 7..."
        # listings. We log them but don't emit a node — they have no
        # useful content for downstream consumers.
        if role == BlockRole.index:
            logger.debug(
                "Dropping index block %s: %s", block.block_id, block.text[:60]
            )
            continue

        # ---- page-level stuff goes into its own node ----
        if role in (BlockRole.page_header, BlockRole.page_footer):
            finalize(current)
            current = None
            hdr = new_builder(NodeKind.header)
            hdr.body_blocks.append(_make_text_node(block))
            if block.bbox:
                hdr.bboxes.append(block.bbox)
            hdr.block_ids.append(block.block_id)
            hdr.confidence = cb.confidence
            articles.append(hdr)
            continue

        # ---- ad text starts (or extends) an ad node ----
        if role == BlockRole.ad_text:
            if current is None or current.kind != NodeKind.advertisement:
                finalize(current)
                current = new_builder(NodeKind.advertisement)
                current.confidence = cb.confidence
            current.body_blocks.append(_make_text_node(block))
            if block.bbox:
                current.bboxes.append(block.bbox)
            current.block_ids.append(block.block_id)
            continue

        # ---- headline opens a new article ----
        if role == BlockRole.headline:
            finalize(current)
            current = new_builder(NodeKind.article)
            current.headline = _make_text_node(block)
            if block.bbox:
                current.bboxes.append(block.bbox)
            current.block_ids.append(block.block_id)
            current.confidence = cb.confidence
            continue

        # ---- body text attaches to current article ----
        if role == BlockRole.body:
            if current is None or current.kind != NodeKind.article:
                # Orphan body within this column — create an untitled
                # article. Confidence is reduced so downstream consumers
                # (or the future LLM_HOOK) can re-examine these.
                finalize(current)
                current = new_builder(NodeKind.article)
                current.confidence = 0.5
            current.body_blocks.append(_make_text_node(block))
            if block.bbox:
                current.bboxes.append(block.bbox)
            current.block_ids.append(block.block_id)
            continue

        # ---- images attach to current (or create untitled) ----
        if role == BlockRole.image:
            if current is None:
                current = new_builder(NodeKind.article)
                current.confidence = 0.5
            img_id, stored_path = asset_paths.get(
                block.block_id, (block.block_id, block.image_path or "")
            )
            caption_text = block.captions[0] if block.captions else None
            current.images.append(
                _make_image_node(block, img_id, stored_path, caption_text)
            )
            if block.bbox:
                current.bboxes.append(block.bbox)
            current.block_ids.append(block.block_id)
            continue

        # ---- tables attach to current ----
        if role == BlockRole.table:
            if current is None:
                current = new_builder(NodeKind.article)
                current.confidence = 0.5
            raw_html = block.raw.get("table_body", "") or block.text
            if raw_html:
                current.body_blocks.append(
                    TextNode(text=raw_html, provenance=_provenance(block))
                )
            if block.bbox:
                current.bboxes.append(block.bbox)
            current.block_ids.append(block.block_id)
            continue

        # Unknown role -> skip, but log
        logger.debug("Skipping block %s with role %s", block.block_id, role)

    finalize(current)
    return articles


def segment(
    classified: list[ClassifiedBlock],
    asset_paths: dict[str, tuple[str, str]],
    reading_direction: str = "ltr",
) -> list[PageNode]:
    """Run segmentation for every page in the document.

    ``reading_direction`` is passed through to each page so right-to-left
    layouts (traditional Japanese newspapers) get the correct column order.
    """
    per_page = _group_by_page(classified)
    return [
        segment_page(pn, per_page[pn], asset_paths, reading_direction=reading_direction)
        for pn in sorted(per_page.keys())
    ]
