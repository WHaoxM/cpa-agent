"""MinerU local-source prefer (stop ModelScope re-download spam)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.pipeline.entity_extractor import mineru_local as ml


def _touch_markers(kit: Path) -> None:
    for rel in ml._PIPELINE_MARKERS:
        p = kit / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")


def test_prefer_local_promotes_modelscope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cache = tmp_path / "mineru"
    kit = cache / "models" / "OpenDataLab" / "PDF-Extract-Kit-1___0"
    _touch_markers(kit)

    monkeypatch.setenv("MODELSCOPE_CACHE", str(cache))
    monkeypatch.setenv("MINERU_MODEL_SOURCE", "modelscope")
    monkeypatch.delenv("MINERU_TOOLS_CONFIG_JSON", raising=False)

    actions = ml.prefer_local_mineru_models(cache)
    assert actions
    assert os.environ["MINERU_MODEL_SOURCE"] == "local"
    cfg = Path(os.environ["MINERU_TOOLS_CONFIG_JSON"])
    assert cfg.is_file()
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert Path(data["models-dir"]["pipeline"]).resolve() == kit.resolve()


def test_prefer_local_skips_huggingface(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cache = tmp_path / "mineru"
    kit = cache / "models" / "OpenDataLab" / "PDF-Extract-Kit-1.0"
    _touch_markers(kit)
    monkeypatch.setenv("MODELSCOPE_CACHE", str(cache))
    monkeypatch.setenv("MINERU_MODEL_SOURCE", "huggingface")

    assert ml.prefer_local_mineru_models(cache) == []
    assert os.environ["MINERU_MODEL_SOURCE"] == "huggingface"


def test_find_kit_incomplete(tmp_path: Path):
    cache = tmp_path / "mineru"
    kit = cache / "models" / "OpenDataLab" / "PDF-Extract-Kit-1___0"
    (kit / "models" / "Layout").mkdir(parents=True)
    assert ml.find_pipeline_kit_root(cache) is None
