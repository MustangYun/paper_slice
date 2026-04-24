"""Canonical data schemas for paperslice outputs.

Every field the API returns is defined here. Downstream consumers (DB,
LLM pipelines, UIs) should rely on these types, not on raw dicts.

v7 변경점:
- ParseMode enum: API에서 OCR/txt/auto 선택 가능
- BlockDiff, DiffReport: 두 방법 비교 리포트 모델
- ParseResponse.diff_report: 선택적 비교 결과

기존 필드는 전부 그대로. 하위 호환 유지.
"""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------


class BoundingBox(BaseModel):
    """Rectangular region in page pixel coordinates, origin top-left."""

    model_config = ConfigDict(frozen=True)

    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def area(self) -> float:
        return max(self.width, 0) * max(self.height, 0)


class Provenance(BaseModel):
    """Where did this content come from in the source document?"""

    page: int = Field(..., ge=1, description="1-based page number.")
    bbox: BoundingBox | None = Field(
        default=None,
        description="Region on the page. None if MinerU did not emit bbox.",
    )
    block_ids: list[str] = Field(
        default_factory=list,
        description="Internal block IDs that contributed to this node.",
    )


# ---------------------------------------------------------------------------
# Parse mode (v7)
# ---------------------------------------------------------------------------


class ParseMode(str, Enum):
    """Which extraction path to take in MinerU (pipeline backend only).

    - auto: PyMuPDF로 PDF를 살펴서 자동 결정. 디지털 PDF면 txt, 스캔이면 ocr.
    - ocr : 무조건 OCR. 스캔본이거나 텍스트 레이어를 못 믿을 때.
    - txt : 무조건 텍스트 레이어. 디지털 PDF 확실할 때. 빠르고 정확.

    vlm/hybrid 백엔드에서는 이 값을 무시함 (자체적으로 처리).
    """

    auto = "auto"
    ocr = "ocr"
    txt = "txt"


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


class NodeKind(str, Enum):
    """What role does a top-level page node play?"""

    article = "article"
    advertisement = "advertisement"
    header = "header"
    unknown = "unknown"


# ---------------------------------------------------------------------------
# Leaf content nodes
# ---------------------------------------------------------------------------


class TextNode(BaseModel):
    """A single text block (headline, paragraph, caption)."""

    text: str
    provenance: Provenance


class ImageNode(BaseModel):
    """A single image asset extracted from the PDF."""

    image_id: str = Field(
        ...,
        description="Stable ID for this image. Format: p<page>-img-<nn>.",
    )
    stored_path: str = Field(
        ...,
        description=(
            "Relative path (from output_root/<document_id>/) where the "
            "image file is saved. Example: 'images/p1-img-01.jpg'."
        ),
    )
    mime_type: str = Field(default="image/jpeg")
    provenance: Provenance
    caption: TextNode | None = None


# ---------------------------------------------------------------------------
# Top-level page nodes
# ---------------------------------------------------------------------------


class ArticleNode(BaseModel):
    """A single newspaper article or advertisement block."""

    node_id: str = Field(
        ...,
        description="Stable ID. Format: p<page>-<kind_short>-<nn>, e.g. 'p1-art-03'.",
    )
    kind: NodeKind
    confidence: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Classifier confidence. 1.0 means rule-based deterministic.",
    )

    headline: TextNode | None = Field(
        default=None,
        description="Headline/title text. Null for ads/headers without a clear title.",
    )
    body_blocks: list[TextNode] = Field(default_factory=list)
    images: list[ImageNode] = Field(default_factory=list)

    bbox: BoundingBox | None = Field(
        default=None,
        description="Union bbox of all content blocks in this node.",
    )

    metadata: dict[str, str] = Field(
        default_factory=dict,
        description="Free-form labels. E.g. {'advertiser': 'CEMEDINE'} for ads.",
    )


class PageNode(BaseModel):
    """A single page of the source document."""

    page_number: int = Field(..., ge=1)
    width: int | None = None
    height: int | None = None

    nodes: list[ArticleNode] = Field(
        default_factory=list,
        description="Top-level nodes on this page, ordered by reading sequence.",
    )


# ---------------------------------------------------------------------------
# Diff report (v7) — comparing OCR vs txt methods
# ---------------------------------------------------------------------------


class BlockDiff(BaseModel):
    """Single block where ocr and txt methods disagree."""

    page: int
    bbox: BoundingBox | None
    ocr_text: str = Field(
        ...,
        description="Text produced by the OCR method (MinerU -m ocr).",
    )
    txt_text: str = Field(
        ...,
        description="Text produced by the text-layer method (MinerU -m txt).",
    )


class DiffReport(BaseModel):
    """Side-by-side comparison of OCR vs text-layer extraction.

    Populated when `diff_report=true` in the parse request. Each entry in
    `differences` is a block whose text differs between the two methods.
    Note: neither side is claimed as "correct" — this is just a disagree‑
    ment log. The caller decides what to do with it (usually: trust txt
    when the PDF is digital).
    """

    total_blocks_compared: int = Field(
        ...,
        description="Number of blocks present in both ocr and txt runs.",
    )
    differing_blocks: int = Field(
        ...,
        description="Number of those blocks whose text differs.",
    )
    differences: list[BlockDiff] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Top-level response
# ---------------------------------------------------------------------------


class SourceInfo(BaseModel):
    """Metadata about the parse itself."""

    filename: str
    page_count: int
    parser: Literal["mineru"] = "mineru"
    parser_backend: str
    parser_version: str
    parsed_at: str
    mode_used: str = Field(
        default="auto",
        description=(
            "Which parse mode was actually used (auto/ocr/txt). For auto, "
            "this is the resolved method ('ocr' or 'txt') after detection."
        ),
    )


class QualityInfo(BaseModel):
    """Parse quality signals."""

    status: Literal["success", "partial", "failed"] = "success"
    warnings: list[str] = Field(default_factory=list)


class ParseResponse(BaseModel):
    """Top-level parse response."""

    document_id: str
    source: SourceInfo
    pages: list[PageNode]
    quality: QualityInfo
    assets_dir: str = Field(
        ...,
        description=(
            "Relative path (from output_root) where assets for this document "
            "are stored. Clients can mount the same volume to read them."
        ),
    )
    diff_report: DiffReport | None = Field(
        default=None,
        description=(
            "Side-by-side ocr vs txt comparison. Only present when "
            "diff_report=true was requested."
        ),
    )
