"""Local human-reference OCR review bundle; never prints private page text.

Prepare creates page PNGs and BLANK human references, optionally real sidecar
hypotheses. Score refuses unreviewed references, changed pages and manifests.
RapidFuzz supplies Levenshtein rather than a parallel edit-distance algorithm.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import unicodedata

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(pdf: Path, output: Path, *, ocr: bool = False):
    import pymupdf
    from app.pipeline.entity_extractor.pdf_sidecar import parse_pdf_via_sidecar

    # Refuse overwrite: existing manual annotations are irreplaceable.
    output.mkdir(parents=True, exist_ok=False)
    for folder in ("images", "human", "ocr"):
        (output / folder).mkdir()
    manifest = {"schema": 1, "source_sha256": digest(pdf), "source_pages": 0,
                "reference_origin": "independent_human_transcription",
                "annotator": "", "reviewer": "", "pages": []}
    with pymupdf.open(pdf) as document:
        manifest["source_pages"] = len(document)
        for index, page in enumerate(document):
            stem = f"page_{index + 1:03d}"
            image = output / "images" / f"{stem}.png"
            page.get_pixmap(matrix=pymupdf.Matrix(2, 2)).save(image)
            (output / "human" / f"{stem}.txt").write_text("", encoding="utf-8")
            item = {"page": stem, "image_sha256": digest(image), "reviewed": False,
                    "reference_sha256": "", "ocr_sha256": "", "ocr_status": "not_run"}
            if ocr:
                temporary = output / f"{stem}.pdf"
                try:
                    with pymupdf.open() as one:
                        one.insert_pdf(document, from_page=index, to_page=index)
                        one.save(temporary)
                    result = parse_pdf_via_sidecar(str(temporary))
                    if result.get("success"):
                        hypothesis = output / "ocr" / f"{stem}.txt"
                        hypothesis.write_text(result["text"], encoding="utf-8")
                        item.update(ocr_status="processed", ocr_sha256=digest(hypothesis),
                                    ocr_engine=result.get("engine"))
                    else:
                        item["ocr_status"] = "failed"
                finally:
                    temporary.unlink(missing_ok=True)
            manifest["pages"].append(item)
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {"status": "awaiting_human_transcription", "pages": len(manifest["pages"]),
            "ocr_pages": sum(p["ocr_status"] == "processed" for p in manifest["pages"])}


def score(output: Path, source: Path):
    from rapidfuzz.distance import Levenshtein

    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    def rejected(reason):
        return {"status": "not_accepted", "reason": reason, "cer": None}
    if manifest.get("source_sha256") != digest(source):
        return rejected("source_changed")
    if (manifest.get("reference_origin") != "independent_human_transcription"
            or not manifest.get("annotator") or not manifest.get("reviewer")
            or manifest["annotator"] == manifest["reviewer"]):
        return rejected("independent_human_review_missing")
    pages = manifest.get("pages") or []
    expected = [f"page_{i + 1:03d}" for i in range(manifest.get("source_pages", 0))]
    if not expected or [p.get("page") for p in pages] != expected:
        return rejected("page_manifest_mismatch")
    for folder, extension in (("human", "txt"), ("ocr", "txt"), ("images", "png")):
        if sorted(p.stem for p in (output / folder).glob(f"page_*.{extension}")) != expected:
            return rejected("page_manifest_mismatch")
    totals = {label: {"edits": 0, "reference_chars": 0} for label in ("strict", "normalized")}
    scores = []
    for item in pages:
        stem = item["page"]
        human = output / "human" / f"{stem}.txt"
        hypothesis = output / "ocr" / f"{stem}.txt"
        image = output / "images" / f"{stem}.png"
        if (item.get("reviewed") is not True or item.get("reference_sha256") != digest(human)
                or item.get("image_sha256") != digest(image)
                or item.get("ocr_sha256") != digest(hypothesis)
                or item.get("ocr_status") != "processed"):
            return rejected("unreviewed_or_changed_page")
        reference = human.read_text(encoding="utf-8")
        actual = hypothesis.read_text(encoding="utf-8")
        page_score = {"page": stem}
        for label in totals:
            normalize = lambda s: "".join(unicodedata.normalize("NFC", s).split())
            ref, hyp = (reference, actual) if label == "strict" else (normalize(reference), normalize(actual))
            if not ref:
                return rejected("empty_human_reference")
            edits = Levenshtein.distance(ref, hyp)
            page_score[label] = {"edits": edits, "reference_chars": len(ref), "cer": edits / len(ref)}
            totals[label]["edits"] += edits
            totals[label]["reference_chars"] += len(ref)
        scores.append(page_score)
    for total in totals.values():
        total["cer"] = total["edits"] / total["reference_chars"]
    return {"status": "scored", "pages": scores, "aggregate": totals,
            "reference_provenance": "human_attestation_not_cryptographic_identity"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "score"))
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ocr", action="store_true")
    args = parser.parse_args()
    result = prepare(args.source, args.output, ocr=args.ocr) if args.command == "prepare" else score(args.output, args.source)
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result["status"] == "not_accepted" else 0


if __name__ == "__main__":
    raise SystemExit(main())
