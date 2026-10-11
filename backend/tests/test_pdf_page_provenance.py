"""PDF text-layer extraction must preserve page provenance for resume review."""

import pymupdf

from app.pipeline.entity_extractor.l0_full_parser import L0FullParser


def test_pymupdf_keeps_text_blocks_on_their_source_pages(tmp_path):
    path = tmp_path / "three-pages.pdf"
    doc = pymupdf.open()
    for page_number in range(1, 4):
        page = doc.new_page()
        page.insert_text((72, 72), f"Unique page {page_number} resume evidence")
    doc.save(path)
    doc.close()

    result = L0FullParser()._parse_pdf_pymupdf(str(path))

    assert result.success is True
    assert result.metadata["pages"] == 3
    assert {block.page for block in result.blocks} == {0, 1, 2}
    assert len({block.block_id for block in result.blocks}) == len(result.blocks)
    for page_number in range(1, 4):
        assert any(
            f"Unique page {page_number}" in block.text and block.page == page_number - 1
            for block in result.blocks
        )


def test_pymupdf_does_not_attribute_blank_page_to_another_page(tmp_path):
    path = tmp_path / "blank-middle.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "First page evidence")
    doc.new_page()
    doc.new_page().insert_text((72, 72), "Third page evidence")
    doc.save(path)
    doc.close()

    result = L0FullParser()._parse_pdf_pymupdf(str(path))

    assert result.success is True
    assert result.metadata["pages"] == 3
    assert {block.page for block in result.blocks} == {0, 2}
    assert "Third page evidence" in result.text
