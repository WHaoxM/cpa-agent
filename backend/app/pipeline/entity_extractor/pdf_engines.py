"""
L0 PDF 解析引擎能力登记（capability registry）。

模式升级锁定：
- auto = Sidecar → PyMuPDF（进程内 MinerU 不在 auto 阶梯）
- mineru 仅显式 CPA_L0_PDF_ENGINE=mineru 时启用（遗留/实验）
- Sidecar 未配置或不可达 → DISABLED，不阻塞启动
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple

from app.utils.logger import get_logger

logger = get_logger("cpa-agent.pdf_engines")


class EngineStatus(str, Enum):
    UNKNOWN = "unknown"
    READY = "ready"
    DISABLED = "disabled"  # 策略/环境强制关闭
    FAILED = "failed"  # 运行时硬失败，粘性


@dataclass(frozen=True)
class EngineState:
    name: str
    status: EngineStatus
    reason: str = ""
    formula_enable: bool = False


_lock = threading.Lock()
_states: dict[str, EngineState] = {}


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def pdf_engine_preference() -> str:
    """auto | sidecar | pymupdf | mineru"""
    return (os.environ.get("CPA_L0_PDF_ENGINE") or "auto").strip().lower()


def mineru_formula_enabled() -> bool:
    """简历默认 False：公式模型是当前最脆的依赖面。"""
    return _env_bool("CPA_L0_MINERU_FORMULA", False)


def probe_mineru_capability(formula_enable: bool) -> Tuple[bool, str]:
    """深度能力探测：仅验证 import 边界。"""
    try:
        from mineru.cli.common import do_parse  # noqa: F401
    except Exception as e:
        return False, f"mineru.cli.common import failed: {e}"

    if formula_enable:
        try:
            from transformers.pytorch_utils import (  # noqa: F401
                find_pruneable_heads_and_indices,
            )
        except Exception as e:
            return (
                False,
                "formula stack incompatible (transformers missing "
                f"find_pruneable_heads_and_indices): {e}",
            )
        try:
            import importlib

            importlib.import_module(
                "mineru.model.mfr.unimernet.unimernet_hf.unimer_swin.modeling_unimer_swin"
            )
        except Exception as e:
            return False, f"unimernet import failed: {e}"

    return True, "ok"


def get_sidecar_state(*, force_probe: bool = False) -> EngineState:
    with _lock:
        cached = _states.get("sidecar")
        if cached is not None and not force_probe and cached.status != EngineStatus.UNKNOWN:
            return cached

        pref = pdf_engine_preference()
        if pref == "pymupdf":
            state = EngineState("sidecar", EngineStatus.DISABLED, "CPA_L0_PDF_ENGINE=pymupdf")
            _states["sidecar"] = state
            return state
        if pref == "mineru":
            state = EngineState(
                "sidecar",
                EngineStatus.DISABLED,
                "CPA_L0_PDF_ENGINE=mineru (explicit in-process)",
            )
            _states["sidecar"] = state
            return state

        from .pdf_sidecar import probe_sidecar

        status_s, reason = probe_sidecar()
        status = {
            "ready": EngineStatus.READY,
            "disabled": EngineStatus.DISABLED,
            "failed": EngineStatus.FAILED,
        }.get(status_s, EngineStatus.DISABLED)
        state = EngineState("sidecar", status, reason)
        _states["sidecar"] = state
        if status == EngineStatus.READY:
            logger.info("Parse Sidecar READY: %s", reason)
        else:
            logger.info("Parse Sidecar %s: %s", status.value, reason)
        return state


def mark_sidecar_failed(reason: str) -> EngineState:
    with _lock:
        state = EngineState("sidecar", EngineStatus.FAILED, (reason or "runtime failure")[:500])
        _states["sidecar"] = state
        logger.warning("Sidecar marked FAILED sticky: %s", state.reason)
        return state


def get_mineru_state(*, force_probe: bool = False) -> EngineState:
    """进程级 MinerU 状态；仅显式 mineru 偏好时 READY，auto 下 DISABLED。"""
    with _lock:
        cached = _states.get("mineru")
        if cached is not None and not force_probe and cached.status != EngineStatus.UNKNOWN:
            return cached

        pref = pdf_engine_preference()
        formula = mineru_formula_enabled()

        # Pattern-upgrade: MinerU retired from auto ladder
        if pref in ("auto", "sidecar", "pymupdf"):
            state = EngineState(
                "mineru",
                EngineStatus.DISABLED,
                f"retired from auto ladder (CPA_L0_PDF_ENGINE={pref}); use sidecar|pymupdf",
                formula,
            )
            _states["mineru"] = state
            return state

        if pref != "mineru":
            state = EngineState(
                "mineru",
                EngineStatus.DISABLED,
                f"unknown CPA_L0_PDF_ENGINE={pref}",
                formula,
            )
            _states["mineru"] = state
            return state

        if os.name == "nt":
            try:
                from .modelscope_win_links import ensure_modelscope_windows_links

                ensure_modelscope_windows_links()
            except Exception as e:
                logger.debug("modelscope win link fix skipped: %s", e)

        try:
            from .mineru_local import prefer_local_mineru_models

            prefer_local_mineru_models()
        except Exception as e:
            logger.debug("mineru local prefer skipped: %s", e)

        ok, reason = probe_mineru_capability(formula)
        if ok:
            state = EngineState("mineru", EngineStatus.READY, reason, formula)
            logger.info("MinerU capability READY (explicit opt-in, formula_enable=%s)", formula)
        else:
            state = EngineState("mineru", EngineStatus.FAILED, reason, formula)
            logger.warning("MinerU capability FAILED sticky: %s", reason)
        _states["mineru"] = state
        return state


def mineru_ready() -> bool:
    return get_mineru_state().status == EngineStatus.READY


def sidecar_ready() -> bool:
    return get_sidecar_state().status == EngineStatus.READY


def _is_permanent_mineru_failure(reason: str) -> bool:
    r = (reason or "").lower()
    return any(
        k in r
        for k in (
            "import",
            "incompatible",
            "find_pruneable",
            "no module named",
            "cannot import name",
        )
    )


def mark_mineru_failed(reason: str, *, permanent: Optional[bool] = None) -> EngineState:
    with _lock:
        prev = _states.get("mineru")
        formula = prev.formula_enable if prev else mineru_formula_enabled()
        sticky = _is_permanent_mineru_failure(reason) if permanent is None else permanent
        if sticky:
            state = EngineState(
                "mineru",
                EngineStatus.FAILED,
                (reason or "runtime failure")[:500],
                formula,
            )
            _states["mineru"] = state
            logger.warning("MinerU marked FAILED sticky: %s", state.reason)
            return state
        state = EngineState(
            "mineru",
            EngineStatus.READY,
            f"runtime_fault_non_sticky: {(reason or '')[:200]}",
            formula,
        )
        _states["mineru"] = state
        return state


def reset_engine_states() -> None:
    """测试用：清空进程登记。"""
    with _lock:
        _states.clear()


def describe_pdf_engines() -> dict:
    """运维/健康检查用快照。"""
    sc = get_sidecar_state()
    st = get_mineru_state()
    return {
        "preference": pdf_engine_preference(),
        "ladder": ["sidecar", "pymupdf"] if pdf_engine_preference() == "auto" else [pdf_engine_preference()],
        "sidecar": {
            "status": sc.status.value,
            "reason": sc.reason,
        },
        "mineru": {
            "status": st.status.value,
            "reason": st.reason,
            "formula_enable": st.formula_enable,
            "in_auto_ladder": False,
        },
        "fallback": "pymupdf",
    }
