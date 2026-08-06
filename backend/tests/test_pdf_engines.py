"""L0 PDF engine capability registry — capability over import, sticky fail."""

from app.pipeline.entity_extractor import pdf_engines as pe


def setup_function():
    pe.reset_engine_states()


def teardown_function():
    pe.reset_engine_states()


def test_pymupdf_preference_disables_mineru(monkeypatch):
    monkeypatch.setenv("CPA_L0_PDF_ENGINE", "pymupdf")
    st = pe.get_mineru_state(force_probe=True)
    assert st.status == pe.EngineStatus.DISABLED
    assert not pe.mineru_ready()


def test_formula_probe_fails_when_symbol_missing(monkeypatch):
    # ADR-0002: formula probing only runs when MinerU is explicitly opted in.
    monkeypatch.setenv("CPA_L0_PDF_ENGINE", "mineru")
    monkeypatch.setenv("CPA_L0_MINERU_FORMULA", "1")

    def _fake_probe(formula_enable: bool):
        assert formula_enable is True
        return False, "formula stack incompatible: missing symbol"

    monkeypatch.setattr(pe, "probe_mineru_capability", _fake_probe)
    pe.reset_engine_states()
    st = pe.get_mineru_state(force_probe=True)
    assert st.status == pe.EngineStatus.FAILED
    assert "formula" in st.reason


def test_sticky_failed_skips_reprobe(monkeypatch):
    monkeypatch.setenv("CPA_L0_PDF_ENGINE", "mineru")
    # Only permanent (import/stack) failures are sticky; generic runtime faults are not.
    pe.mark_mineru_failed("mineru.cli.common import failed", permanent=True)
    calls = {"n": 0}

    def _probe(_formula):
        calls["n"] += 1
        return True, "should not run"

    monkeypatch.setattr(pe, "probe_mineru_capability", _probe)
    st = pe.get_mineru_state()  # not force
    assert st.status == pe.EngineStatus.FAILED
    assert calls["n"] == 0


def test_parse_pdf_uses_fallback_when_mineru_failed(monkeypatch):
    from app.pipeline.entity_extractor.l0_full_parser import L0FullParser, L0Result

    pe.mark_mineru_failed("mineru.cli.common import failed", permanent=True)
    parser = L0FullParser()
    called = {"mineru": 0, "pymupdf": 0}

    def _m(*a, **k):
        called["mineru"] += 1
        return L0Result(True, "m", [], {}, "mineru")

    def _p(*a, **k):
        called["pymupdf"] += 1
        return L0Result(True, "p", [], {}, "pymupdf")

    monkeypatch.setattr(parser, "_parse_pdf_mineru", _m)
    monkeypatch.setattr(parser, "_parse_pdf_pymupdf", _p)
    out = parser._parse_pdf("dummy.pdf")
    assert called["mineru"] == 0
    assert called["pymupdf"] == 1
    assert out.engine == "pymupdf"


def test_runtime_failure_falls_back_and_is_not_sticky(monkeypatch):
    from app.pipeline.entity_extractor.l0_full_parser import L0FullParser, L0Result
    import types
    import sys

    pe.reset_engine_states()
    with pe._lock:
        pe._states["mineru"] = pe.EngineState(
            "mineru", pe.EngineStatus.READY, "test", False
        )

    parser = L0FullParser()
    monkeypatch.setattr(
        parser,
        "_parse_pdf_pymupdf",
        lambda *a, **k: L0Result(True, "p", [], {}, "pymupdf"),
    )

    fake = types.ModuleType("pymupdf")

    def _boom(*a, **k):
        raise RuntimeError("model init exploded")

    fake.open = _boom
    monkeypatch.setitem(sys.modules, "pymupdf", fake)

    out = parser._parse_pdf_mineru("x.pdf", formula_enable=False)
    assert out.engine == "pymupdf"
    # ADR-0002: a runtime fault is NOT sticky — later resumes must still try MinerU.
    st = pe.get_mineru_state()
    assert st.status == pe.EngineStatus.READY
    assert "runtime_fault_non_sticky" in st.reason
