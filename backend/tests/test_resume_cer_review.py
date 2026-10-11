import importlib.util
import json
from pathlib import Path

import pymupdf


spec = importlib.util.spec_from_file_location("cer_review", Path(__file__).with_name("fixtures") / "resume_cer_review.py")
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)


def test_real_source_review_manifest_and_tamper_detection(tmp_path):
    source = tmp_path / "source.pdf"
    with pymupdf.open() as doc:
        doc.new_page().insert_text((50, 50), "ABC")
        doc.save(source)
    output = tmp_path / "review"
    assert review.prepare(source, output)["status"] == "awaiting_human_transcription"
    assert review.score(output, source)["reason"] == "independent_human_review_missing"
    human = output / "human/page_001.txt"
    hypothesis = output / "ocr/page_001.txt"
    human.write_text("ABC", encoding="utf-8")
    hypothesis.write_text("ADC", encoding="utf-8")
    path = output / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest.update(annotator="synthetic-test-annotator", reviewer="synthetic-test-reviewer")
    manifest["pages"][0].update(reviewed=True, reference_sha256=review.digest(human),
                                ocr_sha256=review.digest(hypothesis), ocr_status="processed")
    path.write_text(json.dumps(manifest), encoding="utf-8")
    score = review.score(output, source)
    assert score["status"] == "scored"
    assert score["aggregate"]["strict"]["cer"] == 1 / 3
    human.write_text("ADDED", encoding="utf-8")
    assert review.score(output, source)["reason"] == "unreviewed_or_changed_page"
