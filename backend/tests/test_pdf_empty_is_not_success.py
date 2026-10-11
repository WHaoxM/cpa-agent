"""An OCR outage must not turn a scanned resume into an empty success."""

import pymupdf

from app.pipeline.entity_extractor.l0_full_parser import L0FullParser


def test_blank_pdf_text_layer_is_not_a_success(tmp_path):
    path = tmp_path / "scan.pdf"
    doc = pymupdf.open()
    doc.new_page()
    doc.save(path)
    doc.close()

    result = L0FullParser()._parse_pdf_pymupdf(str(path))

    assert result.success is False
    assert result.engine == "pymupdf"
    assert result.blocks == []
    assert result.metadata["source_complete"] is False


def test_empty_sidecar_payload_is_not_a_success(monkeypatch):
    from app.pipeline.entity_extractor import pdf_sidecar

    monkeypatch.setattr(pdf_sidecar, "parse_pdf_via_sidecar", lambda _: {
        "success": True, "text": "", "blocks": [], "metadata": {"pages": 2},
    })
    result = L0FullParser()._parse_pdf_sidecar("scan.pdf")

    assert result.success is False
    assert result.engine == "sidecar"
    assert result.metadata["source_complete"] is False
