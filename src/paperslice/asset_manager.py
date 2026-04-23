"""Persist extracted images to the output volume and assign stable IDs.

MinerU writes images into a scratch directory that we delete after the
parse. This module is responsible for copying them out to a persistent
path (under `output_root/<document_id>/images/`) before that happens,
and producing a mapping from block_id -> (image_id, stored_path) that
the segmenter uses when building ImageNodes.
"""
from __future__ import annotations

import logging
import mimetypes
import shutil
from pathlib import Path

from .block_enricher import EnrichedBlock

logger = logging.getLogger(__name__)


def persist_images(
    blocks: list[EnrichedBlock],
    raw_output_dir: Path,
    document_output_dir: Path,
) -> dict[str, tuple[str, str]]:
    """Copy images from MinerU's scratch location to persistent storage.

    Args:
        blocks: All enriched blocks (only image-kind ones are processed).
        raw_output_dir: Where MinerU put its output (contains `images/`).
        document_output_dir: Persistent per-document directory. Images
            will land under `<document_output_dir>/images/`.

    Returns:
        Mapping of `block_id` -> (image_id, stored_path).
        `stored_path` is RELATIVE to `document_output_dir` — consumers
        join it with their own volume mount.
    """
    images_dir = document_output_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    # Count images per page so we can emit p<page>-img-<nn>.
    page_counters: dict[int, int] = {}
    mapping: dict[str, tuple[str, str]] = {}

    for block in blocks:
        if block.kind != "image" or not block.image_path:
            continue

        src = raw_output_dir / block.image_path
        if not src.exists():
            # MinerU may reference images it failed to extract; don't crash.
            logger.warning(
                "Image referenced by block %s not found at %s; skipping",
                block.block_id,
                src,
            )
            continue

        page_counters[block.page] = page_counters.get(block.page, 0) + 1
        n = page_counters[block.page]
        image_id = f"p{block.page}-img-{n:02d}"

        # Preserve original extension (MinerU usually emits .jpg)
        ext = src.suffix.lower() or ".jpg"
        dst_rel = Path("images") / f"{image_id}{ext}"
        dst = document_output_dir / dst_rel

        shutil.copy2(src, dst)
        logger.debug("Persisted image %s -> %s", src, dst)

        mapping[block.block_id] = (image_id, str(dst_rel).replace("\\", "/"))

    return mapping


def guess_mime_type(stored_path: str) -> str:
    """Best-effort mime type from the stored image filename."""
    mime, _ = mimetypes.guess_type(stored_path)
    return mime or "image/jpeg"
