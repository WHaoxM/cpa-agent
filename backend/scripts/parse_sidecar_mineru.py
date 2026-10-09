"""Real local PDF OCR Sidecar backed by the project's installed MinerU.

Run with the mkg interpreter from repo root:
    python -B backend/scripts/parse_sidecar_mineru.py

This service refuses to start OCR without a complete *local* PDF-Extract-Kit.
It never silently downloads models or returns placeholder text.
"""

from __future__ import annotations

import os
import sys
import tempfile
import uuid
from pathlib import Path

from flask import Flask, jsonify, request

# `python backend/scripts/parse_sidecar_mineru.py` places scripts/, not backend/,
# on sys.path. Make the local run command independent of PYTHONPATH.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.pipeline.entity_extractor.mineru_local import (
    find_pipeline_kit_root,
    prefer_local_mineru_models,
)


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024


def _capability() -> tuple[bool, str]:
    kit = find_pipeline_kit_root()
    if kit is None:
        return False, "local PDF-Extract-Kit model files are incomplete or MODELSCOPE_CACHE is unset"
    prefer_local_mineru_models(force=True)
    if os.environ.get("MINERU_MODEL_SOURCE") != "local":
        return False, "MinerU model source is not local"
    from app.pipeline.entity_extractor.pdf_engines import probe_mineru_capability

    return probe_mineru_capability(formula_enable=False)


@app.get("/v1/health")
def health():
    ready, reason = _capability()
    return jsonify({
        "status": "ready" if ready else "unavailable",
        "engine": "mineru",
        "ocr_pdf": ready,
        "reason": reason,
    }), 200 if ready else 503


@app.post("/api/cpa/parse")
def parse_pdf():
    ready, reason = _capability()
    if not ready:
        return jsonify({"error": reason, "engine": "mineru"}), 503
    upload = request.files.get("file")
    if upload is None or Path(upload.filename or "").suffix.lower() != ".pdf":
        return jsonify({"error": "PDF multipart file required", "engine": "mineru"}), 400

    # Force this isolated Sidecar to use MinerU. The Flask backend remains on
    # ADR-0002's auto => Sidecar => PyMuPDF ladder.
    os.environ["CPA_L0_PDF_ENGINE"] = "mineru"
    from app.pipeline.entity_extractor.pdf_engines import reset_engine_states
    from app.pipeline.entity_extractor.l0_full_parser import L0FullParser

    reset_engine_states()
    with tempfile.TemporaryDirectory(prefix="cpa-parse-") as directory:
        path = Path(directory) / f"{uuid.uuid4().hex}.pdf"
        upload.save(path)
        result = L0FullParser().parse_file(path)

    # L0 can fall back to PyMuPDF after a MinerU failure. That is not OCR;
    # reject it instead of returning a false-positive complete document.
    if not result.success or result.engine != "mineru" or not (result.text or "").strip():
        return jsonify({
            "error": result.error or f"MinerU OCR unavailable; fallback engine={result.engine}",
            "engine": result.engine,
        }), 503
    return jsonify({
        "text": result.text,
        "engine": "mineru",
        "blocks": [
            {"text": block.text, "page": block.page, "type": block.block_type}
            for block in result.blocks
        ],
    })


if __name__ == "__main__":
    port = int(os.environ.get("CPA_PARSE_SIDECAR_PORT") or "9380")
    app.run(host="127.0.0.1", port=port, threaded=False)
