"""
Windows ModelScope cache hygiene for MinerU.

ModelScope stores repos as ``Name___x`` (dots → underscores) and tries to
symlink the friendly ``Name.x`` path. On Windows those symlinks often point at
a stale path (e.g. old ``modelscope_cache/``) → broken link → endless re-download.

Replace broken / stale SYMLINKD with a directory junction to the real cache
folder. Junctions do not need Developer Mode.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Iterable, List, Tuple

from app.utils.logger import get_logger

logger = get_logger("cpa-agent.modelscope_win")

_DOT_ALIAS = re.compile(r"^(.+?)___(\d+)$")


def ensure_modelscope_windows_links(cache_root: str | Path | None = None) -> List[str]:
    """Scan MODELSCOPE_CACHE and fix OpenDataLab-style friendly names."""
    if os.name != "nt":
        return []

    # Prefer no symlink attempts when ModelScope honors these
    os.environ.setdefault("MODELSCOPE_HUB_CREATE_SYMLINK", "0")
    os.environ.setdefault("MODELSCOPE_SYMLINK", "0")
    os.environ.setdefault("MODELSCOPE_SYMLINK_FILES_IN_ROOT_ENABLED", "false")

    root = Path(cache_root or os.environ.get("MODELSCOPE_CACHE") or "").expanduser()
    if not root.is_dir():
        return []

    actions: List[str] = []
    for parent, real_name, friendly_name in _iter_alias_pairs(root):
        real = parent / real_name
        friendly = parent / friendly_name
        if not real.is_dir():
            continue
        msg = _ensure_junction(friendly, real)
        if msg:
            actions.append(msg)
            logger.info("%s", msg)
    return actions


def _iter_alias_pairs(root: Path) -> Iterable[Tuple[Path, str, str]]:
    for parent in root.rglob("*"):
        if not parent.is_dir():
            continue
        # Skip ModelScope temp download trees
        if "._____temp" in parent.parts:
            continue
        try:
            names = [p.name for p in parent.iterdir()]
        except OSError:
            continue
        for name in names:
            m = _DOT_ALIAS.match(name)
            if not m:
                continue
            yield parent, name, f"{m.group(1)}.{m.group(2)}"


def _is_reparse_point(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        r = subprocess.run(
            ["fsutil", "reparsepoint", "query", str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        return r.returncode == 0 and "Reparse Tag" in (r.stdout or "")
    except Exception:
        return False


def _resolve_ok(link: Path, target: Path) -> bool:
    try:
        return link.exists() and link.resolve() == target.resolve()
    except OSError:
        return False


def _remove_path(path: Path) -> None:
    """Remove file, symlink, junction, or empty directory."""
    if path.is_symlink() or _is_reparse_point(path):
        # SYMLINKD / junction: rmdir; file symlink: unlink
        subprocess.run(
            ["cmd", "/c", "rmdir", str(path)],
            check=False,
            capture_output=True,
        )
        if path.exists() or path.is_symlink() or _is_reparse_point(path):
            try:
                path.unlink()
            except OSError:
                pass
        return
    if path.is_file():
        path.unlink()
        return
    if path.is_dir():
        subprocess.run(
            ["cmd", "/c", "rmdir", str(path)],
            check=False,
            capture_output=True,
        )


def _ensure_junction(friendly: Path, real: Path) -> str:
    if _resolve_ok(friendly, real):
        return ""

    if friendly.exists() or friendly.is_symlink() or _is_reparse_point(friendly):
        # Ordinary non-empty directory (active download) — do not destroy
        if (
            friendly.is_dir()
            and not friendly.is_symlink()
            and not _is_reparse_point(friendly)
        ):
            try:
                if any(friendly.iterdir()):
                    return (
                        f"skip {friendly}: ordinary dir present; "
                        f"delete it to link → {real.name}"
                    )
            except OSError:
                return f"skip {friendly}: cannot inspect"
        try:
            _remove_path(friendly)
        except OSError as e:
            return f"failed remove {friendly}: {e}"

    r = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(friendly), str(real)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()
        return f"failed junction {friendly.name} -> {real.name}: {err}"
    return f"junction {friendly.name} -> {real.name}"
