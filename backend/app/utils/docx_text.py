"""Thin DOCX adapter: Mammoth body extraction plus python-docx headers/footers.

Mammoth handles OOXML text boxes and preserves body reading order; python-docx
provides the section header/footer API that Mammoth's raw-text output omits.
"""

from __future__ import annotations

from docx import Document
from docx.table import Table
import mammoth


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

    seen = set()
    for section in document.sections:
        header = section.header
        key = id(header._element)
        if key not in seen:
            seen.add(key)
            append_section(header)

    with open(path, "rb") as source:
        body = mammoth.extract_raw_text(source).value
    parts.extend(line.strip() for line in body.splitlines() if line.strip())

    for section in document.sections:
        footer = section.footer
        key = id(footer._element)
        if key not in seen:
            seen.add(key)
            append_section(footer)

    return "\n".join(parts)
