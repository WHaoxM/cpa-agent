"""Thin DOCX adapter: Mammoth body extraction plus python-docx headers/footers.

Mammoth handles OOXML text boxes and preserves body reading order; python-docx
provides the section header/footer API that Mammoth's raw-text output omits.
"""

from __future__ import annotations

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
import mammoth
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable, Optional


def extract_docx_text(path: str) -> str:
    document = Document(path)
    parts = []

    def append_section(container) -> None:
        for block in container.iter_inner_content():
            if isinstance(block, Table):
                for row in block.rows:
                    cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                    if cells:
                        parts.append(" | ".join(cells))
            elif block.text and block.text.strip():
                parts.append(block.text.strip())
        # Header/footer API exposes top-level blocks, not nested text boxes.
        # Delegate paragraph decoding to python-docx for their OOXML text nodes.
        for node in container._element.xpath(".//w:txbxContent//w:p"):
            value = Paragraph(node, container).text.strip()
            if value:
                parts.append(value)

    seen = set()
    for section in document.sections:
        for header in (section.header, section.first_page_header, section.even_page_header):
            key = header._element
            if key not in seen:
                seen.add(key)
                append_section(header)

    with open(path, "rb") as source:
        body = mammoth.extract_raw_text(source).value
    parts.extend(line.strip() for line in body.splitlines() if line.strip())

    for section in document.sections:
        for footer in (section.footer, section.first_page_footer, section.even_page_footer):
            key = footer._element
            if key not in seen:
                seen.add(key)
                append_section(footer)

    return "\n".join(parts)


def _ocr_embedded_image(blob: bytes, content_type: str) -> dict:
    """Reuse the configured OCR service, never a text-only PDF fallback."""
    import pymupdf
    from app.pipeline.entity_extractor.pdf_sidecar import parse_pdf_via_sidecar

    with TemporaryDirectory(prefix="cpa-docx-ocr-") as directory:
        target = Path(directory) / "embedded.pdf"
        # MuPDF performs format detection; unsupported WMF/EMF stay explicit failures.
        with pymupdf.open(stream=blob, filetype=content_type.split("/")[-1]) as image:
            target.write_bytes(image.convert_to_pdf())
        return parse_pdf_via_sidecar(str(target))


def extract_docx_with_media(
    path: str, *, allow_ocr: bool = True,
    image_ocr: Optional[Callable[[bytes, str], dict]] = None,
) -> tuple[str, dict]:
    """Mammoth text + package image parts through the existing OCR sidecar.

    Image transcripts are separate source-part supplements, not invented Word
    pages or a claim of visual reading order. python-docx traverses OPC relations,
    including header/footer pictures, rather than scanning arbitrary ZIP entries.
    """
    text = extract_docx_text(path)
    document = Document(path)
    media = []
    issues = []
    parts = [text] if text.strip() else []
    for part in document.part.package.parts:
        content_type = str(part.content_type)
        # OPC thumbnails describe the file icon; they are not document content.
        if str(part.partname).startswith("/docProps/"):
            continue
        if not content_type.startswith("image/"):
            # Charts/embedded objects are not covered by raster OCR.
            if "/embeddings/" in str(part.partname) or "/charts/" in str(part.partname):
                issues.append({"part": str(part.partname), "reason": "unsupported_embedded_object"})
            continue
        item = {"part": str(part.partname), "content_type": content_type, "status": "pending"}
        media.append(item)
        if not allow_ocr:
            item.update(status="unprocessed", reason="ocr_disabled")
            issues.append(item.copy())
            continue
        try:
            result = (image_ocr or _ocr_embedded_image)(part.blob, content_type)
            extracted = str(result.get("text") or "").strip()
            if not result.get("success") or not extracted:
                raise ValueError(result.get("error") or "ocr_returned_no_text")
            item.update(status="processed", engine=result.get("engine"), chars=len(extracted))
            parts.append(f"[DOCX image {part.partname}]\n{extracted}")
        except Exception as exc:
            item.update(status="failed", reason=type(exc).__name__)
            issues.append(item.copy())
    return "\n\n".join(parts), {
        "engine": "mammoth+python-docx+ocr" if media and allow_ocr else "mammoth+python-docx",
        "source_coverage": "unverified" if issues else "processed",
        "coverage_reason": "docx_parts_unprocessed" if issues else None,
        "docx_media": media,
        "coverage_issues": issues,
        "reading_order": "body_then_image_supplements",
        "accuracy_verified": False,
        "retryable": bool(issues),
    }
