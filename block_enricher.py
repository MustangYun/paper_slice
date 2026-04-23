"""Turn raw MinerU blocks into enriched internal blocks.

Enrichment adds:
- Stable block IDs (so provenance references don't collapse)
- 1-based page numbers (MinerU uses 0-based page_idx)
- Typed bounding boxes
- A consistent `kind` field even across MinerU versions.

Downstream (classifier, segmenter) should consume EnrichedBlock, never
raw dicts. This is the adapter layer that isolates us from MinerU's
internal schema drift.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from .schemas import BoundingBox
from .utils.bbox import normalize_bbox

# MinerU uses these type strings in its content_list output.
BlockKind = Literal["text", "image", "table", "equation", "unknown"]


@dataclass
class EnrichedBlock:
    """A MinerU output block, normalized for paperslice's internal use."""

    block_id: str
    page: int  # 1-based
    kind: BlockKind
    text: str
    bbox: BoundingBox | None

    # MinerU-specific hints we forward to the classifier
    text_level: int | None = None  # 1 usually means heading
    role: str | None = None  # MinerU sometimes labels blocks as 'header', 'ad'

    # Image-specific
    image_path: str | None = None  # relative path inside MinerU output
    captions: list[str] = field(default_factory=list)

    # Original dict in case some module needs fields we didn't preserve
    raw: dict[str, Any] = field(default_factory=dict)


def _infer_kind(raw_type: Any) -> BlockKind:
    """Map MinerU block type to our typed literal."""
    if raw_type in ("text", "image", "table", "equation"):
        return raw_type
    return "unknown"


def enrich_blocks(content_list: list[dict[str, Any]]) -> list[EnrichedBlock]:
    """Convert MinerU's raw content list into our enriched internal form.

    Block IDs are page-local so the same ID can't appear twice in a doc:
        p1-b-001, p1-b-002, ..., p2-b-001, ...
    """
    # Group per page so we can number blocks within each page
    per_page_counter: dict[int, int] = {}
    enriched: list[EnrichedBlock] = []

    for raw in content_list:
        # MinerU uses 0-based page_idx; we standardize on 1-based
        page = int(raw.get("page_idx", 0)) + 1
        per_page_counter[page] = per_page_counter.get(page, 0) + 1
        block_id = f"p{page}-b-{per_page_counter[page]:03d}"

        kind = _infer_kind(raw.get("type"))
        text = (raw.get("text") or raw.get("contents") or "").strip()
        bbox = normalize_bbox(raw.get("bbox") or raw.get("box"))

        # Image captions can appear as list of strings
        captions_raw = raw.get("image_caption") or []
        if isinstance(captions_raw, str):
            captions_raw = [captions_raw]
        captions = [str(c).strip() for c in captions_raw if str(c).strip()]

        enriched.append(
            EnrichedBlock(
                block_id=block_id,
                page=page,
                kind=kind,
                text=text,
                bbox=bbox,
                text_level=raw.get("text_level"),
                role=raw.get("role"),
                image_path=raw.get("img_path") or raw.get("path"),
                captions=captions,
                raw=raw,
            )
        )

    return enriched
