"""Privacy-preserving, per-page extraction audit for local PDF resumes.

This is an inspection tool, not an OCR accuracy benchmark: without a manually
transcribed ground truth, matching another extractor cannot establish accuracy.

Run with the authoritative mkg Python environment, for example::

    python backend/tests/fixtures/resume_page_eval.py resume.pdf \
      --meta resume.meta.json --artifacts resume.artifacts.json \
      --mineru-content /tmp/scan_content_list.json

The JSON output deliberately contains no source text, filenames, or PII.
"""

from __future__ import annotations

import argparse
from collections import Counter
from difflib import SequenceMatcher
import json
from pathlib import Path

import fitz
from pypdf import PdfReader


SECTION_HEADINGS = ("专业技能", "实习经验", "实习经历", "项目经历", "项目经验", "教育经历")


def _read_json(path: Path | None):
    if path is None:
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _normalize(text: str) -> str:
    return "".join(text.split())


def _headings(text: str) -> list[str]:
    return [
        heading for heading, _ in sorted(
            ((heading, text.find(heading)) for heading in SECTION_HEADINGS if heading in text),
            key=lambda entry: entry[1],
        )
    ]


def _per_page(blocks: list[dict], page_count: int) -> list[dict]:
    by_page = {page: [] for page in range(page_count)}
    for block in blocks:
        page = block.get("page_idx", block.get("page"))
        if isinstance(page, int) and page in by_page:
            by_page[page].append(block)

    pages = []
    for page in range(page_count):
        rows = by_page[page]
        texts = [str(row.get("text") or "") for row in rows]
        first_positions = {
            heading: next((index for index, value in enumerate(texts) if heading in value), None)
            for heading in SECTION_HEADINGS
        }
        ys = [row["bbox"][1] for row in rows if isinstance(row.get("bbox"), list) and len(row["bbox"]) >= 4]
        pages.append({
            "page": page + 1,
            "blocks": len(rows),
            "chars": sum(map(len, texts)),
            "section_headings_in_order": [
                heading for heading, _ in sorted(
                    ((heading, index) for heading, index in first_positions.items() if index is not None),
                    key=lambda entry: entry[1],
                )
            ],
            # A backtrack is a signal to inspect reading order; multi-column headers
            # can legitimately produce one and should not be called an OCR error.
            "y_backtracks_over_15pt": (
                sum(ys[i + 1] < ys[i] - 15 for i in range(len(ys) - 1))
                if len(ys) == len(rows) and len(rows) > 1 else None
            ),
        })
    return pages


def evaluate(pdf: Path, meta: Path | None, artifacts: Path | None, mineru_content: Path | None) -> dict:
    with fitz.open(pdf) as doc:
        fitz_texts = [page.get_text() for page in doc]
        page_count = len(doc)
    pypdf_texts = [page.extract_text() or "" for page in PdfReader(str(pdf)).pages]
    result = {
        "pdf_pages": page_count,
        "native_text_chars_per_page": {
            "pymupdf": list(map(len, fitz_texts)),
            "pypdf": list(map(len, pypdf_texts)),
        },
        "native_section_headings_per_page": {
            "pymupdf": [_headings(value) for value in fitz_texts],
            "pypdf": [_headings(value) for value in pypdf_texts],
        },
    }

    meta_data = _read_json(meta)
    if isinstance(meta_data, dict):
        ingest_meta = meta_data.get("ingest_meta") or {}
        stored_blocks = ingest_meta.get("blocks") or []
        result["stored"] = {
            "status": meta_data.get("status"),
            "engine": ingest_meta.get("engine"),
            "reported_block_count": ingest_meta.get("block_count"),
            "actual_block_count": len(stored_blocks),
            "blocks_per_page": _per_page(stored_blocks, page_count),
        }

    stored_text = None
    artifact_data = _read_json(artifacts)
    if isinstance(artifact_data, list):
        for artifact in artifact_data:
            if artifact.get("kind") == "full_text":
                stored_text = str(artifact.get("body_text") or "")
                result.setdefault("stored", {})["full_text_chars"] = len(stored_text)
                result["stored"]["full_text_truncated"] = bool((artifact.get("meta") or {}).get("truncated"))
                result["stored"]["full_text_section_headings"] = _headings(stored_text)
                result["stored"]["native_similarity_no_whitespace"] = {
                    "pymupdf": round(
                        SequenceMatcher(None, _normalize(stored_text), _normalize("\n".join(fitz_texts)), autojunk=False).ratio(), 4
                    ),
                    "pypdf": round(
                        SequenceMatcher(None, _normalize(stored_text), _normalize("\n".join(pypdf_texts)), autojunk=False).ratio(), 4
                    ),
                }
                break

    mineru_data = _read_json(mineru_content)
    if isinstance(mineru_data, list):
        text_blocks = [row for row in mineru_data if row.get("type") == "text"]
        result["mineru"] = {
            "content_types": dict(Counter(str(row.get("type")) for row in mineru_data)),
            "text_blocks_per_page": _per_page(text_blocks, page_count),
        }
        if stored_text is not None:
            current_text = "\n".join(str(row.get("text") or "") for row in text_blocks)
            result["mineru"]["stored_text_similarity_no_whitespace"] = round(
                SequenceMatcher(None, _normalize(stored_text), _normalize(current_text), autojunk=False).ratio(), 4
            )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--meta", type=Path)
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--mineru-content", type=Path)
    args = parser.parse_args()
    print(json.dumps(evaluate(args.pdf, args.meta, args.artifacts, args.mineru_content), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
