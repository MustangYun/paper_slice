"""Classify each enriched block into a role: headline / body / ad / header / etc.

The first-cut strategy is rule-based:
- MinerU-provided signals (text_level, role) are the strongest cue.
- Simple text patterns identify ad-like blocks (phone numbers, URLs,
  boilerplate strings like 'お問い合わせ', copyright footers).
- Header/folio detection looks at small text near the page top/bottom.

Places marked `# LLM_HOOK` are where we can later swap in a LLM classifier
for ambiguous cases.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum

from .block_enricher import EnrichedBlock

logger = logging.getLogger(__name__)


class BlockRole(str, Enum):
    """Per-block role used during segmentation.

    Note: this is *block-level*. The segmenter will later group blocks
    into article/ad/header NodeKinds based on these roles + layout.
    """

    headline = "headline"
    body = "body"
    caption = "caption"
    ad_text = "ad_text"  # text block that is part of an advertisement
    page_header = "page_header"  # masthead, folio, page number
    page_footer = "page_footer"  # copyright line, etc.
    index = "index"  # table-of-contents / "what's on other pages" listing
    image = "image"
    table = "table"
    unknown = "unknown"


@dataclass
class ClassifiedBlock:
    """An enriched block plus the role we think it plays."""

    block: EnrichedBlock
    role: BlockRole
    confidence: float  # 0.0 - 1.0. Rule-based hits are typically >= 0.8


# ---------------------------------------------------------------------------
# Ad detection patterns
# ---------------------------------------------------------------------------

# Japanese boilerplate strings that almost always indicate advertisement/
# contact blocks rather than editorial content.
_AD_PHRASES = (
    "お問い合わせ",
    "お問合わせ",
    "ホームページ",
    "代表取締役",
    "FAX",
    "TEL",
    "電話",
    "E-mail",
    "e-mail",
    "http://",
    "https://",
    "www.",
    "株式会社",  # company name — weak signal, combined with others
)

_PHONE_RE = re.compile(r"\d{2,4}[-(\s]?\d{2,4}[-)\s]?\d{3,4}")
_URL_RE = re.compile(r"(https?://|www\.)\S+", re.IGNORECASE)


def _looks_like_ad(text: str) -> tuple[bool, float]:
    """Heuristic ad detector. Returns (is_ad, confidence)."""
    hits = 0
    if _PHONE_RE.search(text):
        hits += 2
    if _URL_RE.search(text):
        hits += 2
    for phrase in _AD_PHRASES:
        if phrase in text:
            hits += 1
    # Very short lines with company-name markers (株式会社) alone aren't
    # enough; we want multiple signals.
    if hits >= 3:
        return True, min(0.95, 0.6 + 0.1 * hits)
    if hits >= 2 and len(text) < 120:
        return True, 0.75
    return False, 0.0


# ---------------------------------------------------------------------------
# Footer detection
# ---------------------------------------------------------------------------

_FOOTER_PATTERNS = (
    "第三種郵便物認可",
    "化学工業日報",  # Specific to this publication — extend as needed
)


def _looks_like_footer(text: str) -> bool:
    return any(p in text for p in _FOOTER_PATTERNS)


# ---------------------------------------------------------------------------
# Index (table-of-contents) detection
# ---------------------------------------------------------------------------
#
# Japanese newspapers often print a small "what's on other pages" index
# on the front page (and sometimes on section-front pages). These blocks
# are disastrous for article segmentation: MinerU returns them as
# ordinary body text, and the segmenter happily glues them onto a
# neighboring article. They look like:
#
#   次の焦点は原料の価格転嫁 2
#   昭和化工、工場大改修へ100億円投資 4
#   JCR·薗田新社長、R&D投資厚く 7
#   ...
#
# Signals (any ONE strong signal, or two weak signals):
#   - Multiple single-digit / low-double-digit page numbers scattered
#     through the text (≥3 numbers).
#   - Numbers appear at the END of short phrases separated by hard
#     boundaries (newline / comma / ideographic space).
#   - Multiple distinct "headline-ish" fragments in one block (multiple
#     ideographic commas `、` or fullwidth dots `·`).

_PAGE_NUMBER_TOKEN_RE = re.compile(r"(?:^|[\s、。・·])([1-9][0-9]?)(?=$|[\s、。・·])")
_INDEX_SEPARATORS = ("、", "·", "・")


def _looks_like_index(text: str) -> tuple[bool, float]:
    """Heuristic table-of-contents detector.

    Returns (is_index, confidence). Confidence reflects how confident we
    are this block is a TOC entry rather than a legitimate body paragraph.
    """
    if len(text) < 15:
        # Very short text — most likely a caption/subhead, not an index.
        return False, 0.0

    # Count short-number tokens that look like page references.
    page_number_hits = len(_PAGE_NUMBER_TOKEN_RE.findall(text))

    # Count ideographic separators; indexes typically have many.
    separator_hits = sum(text.count(sep) for sep in _INDEX_SEPARATORS)

    # Page-number density: indexes have lots of small numbers relative
    # to total length. Body paragraphs might have one or two dates, but
    # not a cluster.
    density = page_number_hits / max(1, len(text) / 50)

    # Strong signal: many page-number tokens.
    if page_number_hits >= 4:
        return True, min(0.9, 0.6 + 0.05 * page_number_hits)

    # Moderate: several page numbers AND lots of separators AND high
    # density. Body paragraphs rarely satisfy all three.
    if page_number_hits >= 3 and separator_hits >= 3 and density >= 1.5:
        return True, 0.75

    return False, 0.0


# ---------------------------------------------------------------------------
# Main classifier
# ---------------------------------------------------------------------------


def classify_block(block: EnrichedBlock) -> ClassifiedBlock:
    """Assign a role to a single block using rule-based heuristics."""

    # Non-text blocks are trivial
    if block.kind == "image":
        return ClassifiedBlock(block, BlockRole.image, confidence=1.0)
    if block.kind == "table":
        return ClassifiedBlock(block, BlockRole.table, confidence=1.0)

    # MinerU explicit role always wins if present
    if block.role == "header":
        return ClassifiedBlock(block, BlockRole.page_header, confidence=0.95)
    if block.role == "ad":
        return ClassifiedBlock(block, BlockRole.ad_text, confidence=0.95)

    text = block.text
    if not text:
        return ClassifiedBlock(block, BlockRole.unknown, confidence=0.0)

    # Footer patterns
    if _looks_like_footer(text):
        return ClassifiedBlock(block, BlockRole.page_footer, confidence=0.9)

    # Index / table-of-contents blocks (must run BEFORE ad detection,
    # because indexes can contain page numbers that trip the ad
    # heuristic's phone-number regex).
    is_index, index_conf = _looks_like_index(text)
    if is_index:
        return ClassifiedBlock(block, BlockRole.index, confidence=index_conf)

    # Ad-like text
    is_ad, ad_conf = _looks_like_ad(text)
    if is_ad:
        return ClassifiedBlock(block, BlockRole.ad_text, confidence=ad_conf)

    # Headline: MinerU marks headings with text_level == 1 in pipeline mode
    if block.text_level == 1:
        return ClassifiedBlock(block, BlockRole.headline, confidence=0.85)

    # Caption: short text that sits near an image (segmenter can refine)
    # For now, we leave it as body and let segmenter promote to caption.
    # LLM_HOOK: ambiguous blocks (e.g. text_level == 2, or short bold text
    # not matching headline) could be sent to an LLM for adjudication.

    return ClassifiedBlock(block, BlockRole.body, confidence=0.6)


def classify_blocks(blocks: list[EnrichedBlock]) -> list[ClassifiedBlock]:
    """Run the classifier over a whole document."""
    return [classify_block(b) for b in blocks]
