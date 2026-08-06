"""L0 engine ladder: auto prefers sidecar→pymupdf; mineru retired from auto."""

from __future__ import annotations

import pytest

from app.pipeline.entity_extractor.pdf_engines import (
    EngineStatus,
    describe_pdf_engines,
    get_mineru_state,
    get_sidecar_state,
    reset_engine_states,
)


@pytest.fixture(autouse=True)
def _clean_engines(monkeypatch):
    reset_engine_states()
    monkeypatch.delenv("CPA_L0_PARSE_SIDECAR_URL", raising=False)
    monkeypatch.setenv("CPA_L0_PDF_ENGINE", "auto")
    yield
    reset_engine_states()


def test_auto_disables_mineru_and_sidecar_without_url():
    mineru = get_mineru_state(force_probe=True)
    assert mineru.status == EngineStatus.DISABLED
    assert "retired" in (mineru.reason or "").lower() or "auto" in (mineru.reason or "").lower()

    sc = get_sidecar_state(force_probe=True)
    assert sc.status == EngineStatus.DISABLED

    snap = describe_pdf_engines()
    assert snap["fallback"] == "pymupdf"
    assert snap["mineru"]["in_auto_ladder"] is False


def test_explicit_pymupdf_disables_sidecar(monkeypatch):
    monkeypatch.setenv("CPA_L0_PDF_ENGINE", "pymupdf")
    reset_engine_states()
    sc = get_sidecar_state(force_probe=True)
    assert sc.status == EngineStatus.DISABLED
