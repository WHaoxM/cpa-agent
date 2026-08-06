"""
Prefer MinerU ``MINERU_MODEL_SOURCE=local`` when PDF-Extract-Kit is already cached.

With ``modelscope`` / ``huggingface``, each relative model path calls
``snapshot_download`` and logs "Downloading Model..." even when files exist.
That produces the Windows spam loop after the kit is already under
``MODELSCOPE_CACHE``.

Official escape: write ``mineru.json`` ``models-dir.pipeline`` and set source
to ``local`` so ``auto_download_and_get_model_root_path`` returns the root
without touching the hub.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List, Optional, Sequence

from app.utils.logger import get_logger

logger = get_logger("cpa-agent.mineru_local")

_KIT_DIR_NAMES = ("PDF-Extract-Kit-1.0", "PDF-Extract-Kit-1___0")

# Markers that cover pipeline init paths used by mineru 3.0.x (formula model optional).
_PIPELINE_MARKERS: Sequence[str] = (
    "models/Layout/PP-DocLayoutV2/model.safetensors",
    "models/MFR/unimernet_hf_small_2503/model.safetensors",
    "models/OCR/paddleocr_torch/ch_PP-OCRv5_det_infer.pth",
    "models/TabRec/SlanetPlus/slanet-plus.onnx",
    "models/TabRec/UnetStructure/unet.onnx",
    "models/TabCls/paddle_table_cls/PP-LCNet_x1_0_table_cls.onnx",
    "models/OriCls/paddle_orientation_classification/PP-LCNet_x1_0_doc_ori.onnx",
)


def find_pipeline_kit_root(cache_root: str | Path | None = None) -> Optional[Path]:
    """Return PDF-Extract-Kit root under MODELSCOPE_CACHE if present."""
    root = Path(cache_root or os.environ.get("MODELSCOPE_CACHE") or "").expanduser()
    if not root.is_dir():
        return None

    opendatalab = root / "models" / "OpenDataLab"
    if not opendatalab.is_dir():
        # Some layouts store OpenDataLab directly under cache root
        opendatalab = root / "OpenDataLab"
    if not opendatalab.is_dir():
        return None

    for name in _KIT_DIR_NAMES:
        candidate = opendatalab / name
        if candidate.is_dir() and _pipeline_kit_ready(candidate):
            return candidate.resolve()
    return None


def _pipeline_kit_ready(kit_root: Path) -> bool:
    return all((kit_root / rel).is_file() for rel in _PIPELINE_MARKERS)


def write_mineru_local_config(
    pipeline_root: Path,
    *,
    config_path: str | Path | None = None,
) -> Path:
    """Write/merge mineru.json with models-dir.pipeline (absolute path)."""
    cache = Path(os.environ.get("MODELSCOPE_CACHE") or pipeline_root.parents[2])
    path = Path(config_path) if config_path else (cache / "mineru.json")
    path.parent.mkdir(parents=True, exist_ok=True)

    data: dict = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}

    models_dir = data.get("models-dir")
    if not isinstance(models_dir, dict):
        models_dir = {}
    models_dir["pipeline"] = str(pipeline_root.resolve())
    data["models-dir"] = models_dir
    if "config_version" not in data:
        data["config_version"] = "1.3.1"

    path.write_text(json.dumps(data, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")
    return path.resolve()


def prefer_local_mineru_models(
    cache_root: str | Path | None = None,
    *,
    force: bool = False,
) -> List[str]:
    """
    If a complete kit exists, point MinerU at it via local source.

    Skips when ``MINERU_MODEL_SOURCE`` is already ``local``, or when the user
    explicitly chose ``huggingface`` (unless ``force=True``).
    Promotes ``modelscope`` (our download default) → ``local`` once ready.
    """
    actions: List[str] = []
    current = (os.environ.get("MINERU_MODEL_SOURCE") or "").strip().lower()
    if current == "local" and not force:
        cfg = os.environ.get("MINERU_TOOLS_CONFIG_JSON")
        if cfg and Path(cfg).is_file():
            return actions
        # local set but config missing — try to repair below
    if current == "huggingface" and not force:
        return actions

    kit = find_pipeline_kit_root(cache_root)
    if kit is None:
        return actions

    config_path = write_mineru_local_config(kit)
    os.environ["MINERU_TOOLS_CONFIG_JSON"] = str(config_path)
    os.environ["MINERU_MODEL_SOURCE"] = "local"
    msg = f"MINERU_MODEL_SOURCE=local pipeline={kit}"
    actions.append(msg)
    logger.info("%s (config=%s)", msg, config_path)
    return actions
