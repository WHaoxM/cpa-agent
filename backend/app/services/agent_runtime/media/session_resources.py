"""Qoder-style session resource mount: file_id → session_id (disk + MySQL)."""

from __future__ import annotations

import json
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ....utils.logger import get_logger
from .file_store import get_agent_file_service
from .types import MAX_MOUNTED_FILES

logger = get_logger("cpa-agent.agent_runtime.media.resources")

_mem_mounts: Dict[str, List[Dict[str, Any]]] = {}
_lock = threading.Lock()


def _mount_root() -> Path:
    try:
        from ....config import Config
        root = Path(Config.UPLOAD_FOLDER) / "session_mounts"
    except Exception:
        root = Path(__file__).resolve().parents[4] / "uploads" / "session_mounts"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _mount_path(session_id: str) -> Path:
    return _mount_root() / f"{session_id}.json"


def _normalize_path(file_id: str, mount_path: Optional[str]) -> str:
    if mount_path:
        p = mount_path if mount_path.startswith("/") else f"/data/{mount_path.lstrip('/')}"
        if not p.startswith("/data/"):
            p = f"/data/{p.lstrip('/')}"
        return p[:512]
    return f"/data/{file_id}"


def mount_resources(
    session_id: str,
    resources: List[Dict[str, Any]],
    *,
    student_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    if not session_id:
        raise ValueError("session_id required")
    if not student_id:
        raise PermissionError("student_id required for session resource mount")
    from .schema import ensure_media_schema
    ensure_media_schema()
    files = get_agent_file_service()
    existing = list_resources(session_id, student_id=student_id)
    by_file = {r["file_id"]: r for r in existing if r.get("file_id")}

    for raw in resources or []:
        if len(by_file) >= MAX_MOUNTED_FILES and raw.get("file_id") not in by_file:
            break
        file_id = raw.get("file_id")
        if not file_id:
            continue
        meta = files.get(file_id)
        if not meta:
            raise ValueError(f"file not found: {file_id}")
        if meta.get("status") == "failed":
            raise ValueError(
                f"file ingest failed: {file_id} — {meta.get('error') or '无法提取文本'}"
            )
        if meta.get("status") != "ready":
            raise ValueError(f"file not ready: {file_id} ({meta.get('status')})")
        if not meta.get("student_id"):
            raise PermissionError("file missing student_id owner")
        if meta["student_id"] != student_id:
            raise PermissionError("file student_id mismatch")
        path = _normalize_path(file_id, raw.get("mount_path") or raw.get("path"))
        prev = by_file.get(file_id)
        rec = {
            "resource_id": (prev or {}).get("resource_id") or f"res_{uuid.uuid4().hex[:12]}",
            "session_id": session_id,
            "file_id": file_id,
            "type": raw.get("type") or "file",
            "mount_path": path,
            "filename": meta.get("filename"),
            "excerpt": meta.get("excerpt"),
            "created_at": (prev or {}).get("created_at") or datetime.now().isoformat(sep=" "),
        }
        by_file[file_id] = rec

    out = list(by_file.values())
    with _lock:
        _mem_mounts[session_id] = out
    _write_disk(session_id, out)
    for rec in out:
        _persist_mysql(rec)
    return out


def list_resources(
    session_id: str,
    *,
    student_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    if student_id is None:
        raise PermissionError("student_id required for session resource list")
    with _lock:
        if session_id in _mem_mounts:
            return _filter_owner(list(_mem_mounts[session_id]), student_id)

    # Disk catalog
    mp = _mount_path(session_id)
    if mp.is_file():
        try:
            out = json.loads(mp.read_text(encoding="utf-8"))
            if isinstance(out, list):
                with _lock:
                    _mem_mounts[session_id] = out
                return _filter_owner(list(out), student_id)
        except Exception as e:
            logger.info("mount disk read skip: %s", e)

    try:
        from ...db import get_mysql_client

        mysql = get_mysql_client()
        rows = mysql.fetch_all(
            """
            SELECT resource_id, session_id, file_id, resource_type AS type,
                   mount_path, created_at
            FROM session_resource WHERE session_id = :sid
            ORDER BY created_at ASC
            """,
            {"sid": session_id},
        ) or []
        files = get_agent_file_service()
        out = []
        for r in rows:
            meta = files.get(r["file_id"]) or {}
            if not meta.get("student_id") or meta.get("student_id") != student_id:
                continue
            out.append({
                "resource_id": r["resource_id"],
                "session_id": r["session_id"],
                "file_id": r["file_id"],
                "type": r.get("type") or "file",
                "mount_path": r.get("mount_path"),
                "filename": meta.get("filename"),
                "excerpt": meta.get("excerpt"),
                "created_at": str(r.get("created_at") or ""),
            })
        with _lock:
            _mem_mounts[session_id] = out
        _write_disk(session_id, out)
        return _filter_owner(out, student_id)
    except Exception as e:
        logger.info("list_resources mysql skip: %s", e)
        return []


def resolve_attachment_ids(
    session_id: Optional[str],
    attachment_ids: Optional[List[str]],
    *,
    student_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    ids = [x for x in (attachment_ids or []) if x]
    if ids and not student_id:
        raise PermissionError("student_id required for attachment resolution")
    if ids and session_id:
        mount_resources(
            session_id,
            [{"type": "file", "file_id": fid} for fid in ids],
            student_id=student_id,
        )
    return list_resources(session_id, student_id=student_id) if session_id else []


def _filter_owner(rows: List[Dict[str, Any]], student_id: str) -> List[Dict[str, Any]]:
    files = get_agent_file_service()
    out = []
    for row in rows:
        meta = files.get(row.get("file_id") or "") or {}
        if not meta.get("student_id") or meta.get("student_id") != student_id:
            continue
        out.append(row)
    return out


def _write_disk(session_id: str, rows: List[Dict[str, Any]]) -> None:
    try:
        _mount_path(session_id).write_text(
            json.dumps(rows, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as e:
        logger.info("mount disk write skip: %s", e)


def _persist_mysql(rec: Dict[str, Any]) -> None:
    try:
        from ...db import get_mysql_client

        mysql = get_mysql_client()
        mysql.execute(
            """
            INSERT INTO session_resource
            (resource_id, session_id, file_id, resource_type, mount_path, meta_json, created_at)
            VALUES
            (:resource_id, :session_id, :file_id, :resource_type, :mount_path, :meta_json, :created_at)
            ON DUPLICATE KEY UPDATE
              mount_path=VALUES(mount_path),
              meta_json=VALUES(meta_json)
            """,
            {
                "resource_id": rec["resource_id"],
                "session_id": rec["session_id"],
                "file_id": rec["file_id"],
                "resource_type": rec.get("type") or "file",
                "mount_path": rec.get("mount_path"),
                "meta_json": json.dumps(
                    {"filename": rec.get("filename")}, ensure_ascii=False,
                ),
                "created_at": rec.get("created_at"),
            },
            commit=True,
        )
    except Exception as e:
        logger.info("session_resource persist skipped: %s", e)
