"""pdf_chunker 의 분할·페이지 오프셋·이미지 병합 회귀 테스트."""
from __future__ import annotations

from pathlib import Path

import pytest

fitz = pytest.importorskip("fitz")


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    """텍스트만 있는 20페이지 더미 PDF."""
    src = tmp_path / "sample.pdf"
    doc = fitz.open()
    try:
        for i in range(20):
            page = doc.new_page()
            page.insert_text((72, 72), f"Page {i + 1}")
        doc.save(str(src))
    finally:
        doc.close()
    return src


def test_get_page_count(sample_pdf: Path):
    from paperslice.pdf_chunker import get_page_count

    assert get_page_count(sample_pdf) == 20


def test_split_pdf_into_chunks_partitions_correctly(sample_pdf: Path, tmp_path: Path):
    from paperslice.pdf_chunker import get_page_count, split_pdf_into_chunks

    chunks = split_pdf_into_chunks(sample_pdf, tmp_path, chunk_size=5)
    assert len(chunks) == 4
    for idx, chunk in enumerate(chunks):
        assert chunk.index == idx
        assert chunk.page_count == 5
        assert chunk.path.exists()
        assert get_page_count(chunk.path) == 5
    # 경계 체크
    assert chunks[0].start_page == 0 and chunks[0].end_page == 5
    assert chunks[-1].start_page == 15 and chunks[-1].end_page == 20


def test_split_below_threshold_returns_original(sample_pdf: Path, tmp_path: Path):
    """chunk_size 가 전체 페이지보다 크면 분할 없이 원본만 돌려준다."""
    from paperslice.pdf_chunker import split_pdf_into_chunks

    chunks = split_pdf_into_chunks(sample_pdf, tmp_path, chunk_size=100)
    assert len(chunks) == 1
    assert chunks[0].path == sample_pdf


def test_split_with_zero_chunk_size_is_noop(sample_pdf: Path, tmp_path: Path):
    from paperslice.pdf_chunker import split_pdf_into_chunks

    chunks = split_pdf_into_chunks(sample_pdf, tmp_path, chunk_size=0)
    assert len(chunks) == 1
    assert chunks[0].path == sample_pdf


def test_merge_content_lists_offsets_page_idx(sample_pdf: Path, tmp_path: Path):
    from paperslice.pdf_chunker import merge_content_lists, split_pdf_into_chunks

    chunks = split_pdf_into_chunks(sample_pdf, tmp_path, chunk_size=5)
    fake = [
        (chunks[0], [{"page_idx": 0}, {"page_idx": 3}]),
        (chunks[1], [{"page_idx": 0}, {"page_idx": 2}]),
        (chunks[2], [{"page_idx": 4}]),
    ]
    merged = merge_content_lists(fake)
    assert [b["page_idx"] for b in merged] == [0, 3, 5, 7, 14]


def test_merge_chunk_outputs_copies_images_with_unique_prefix(
    sample_pdf: Path, tmp_path: Path
):
    from paperslice.pdf_chunker import merge_chunk_outputs, split_pdf_into_chunks

    chunks = split_pdf_into_chunks(sample_pdf, tmp_path, chunk_size=5)

    # chunk 별 가짜 raw 출력 디렉터리 + 이미지 하나씩 생성
    per_chunk = []
    for chunk in chunks[:2]:
        raw = tmp_path / f"raw{chunk.index}"
        (raw / "images").mkdir(parents=True)
        img = raw / "images" / "pic.jpg"
        img.write_bytes(b"\xff\xd8\xff\xd9")
        content = [
            {"page_idx": 0, "type": "image", "img_path": "images/pic.jpg"},
            {"page_idx": 1, "type": "text"},
        ]
        per_chunk.append((chunk, content, raw))

    merged_dir = tmp_path / "merged"
    out = merge_chunk_outputs(per_chunk, merged_dir)

    # 2 chunks × 2 blocks = 4 blocks
    assert len(out.content_list) == 4
    # page_idx 오프셋 확인
    page_idxs = [b["page_idx"] for b in out.content_list]
    assert page_idxs == [0, 1, 5, 6]
    # 이미지가 unique prefix 로 복사됐는지
    img_paths = [
        b.get("img_path") for b in out.content_list if b.get("img_path")
    ]
    assert img_paths == ["images/c000_pic.jpg", "images/c001_pic.jpg"]
    assert (merged_dir / "images" / "c000_pic.jpg").exists()
    assert (merged_dir / "images" / "c001_pic.jpg").exists()
    # merged_content_list.json 도 남아야 debug 엔드포인트가 읽을 수 있다
    assert (merged_dir / "merged_content_list.json").exists()
