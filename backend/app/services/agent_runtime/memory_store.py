"""
Qoder-aligned Memory Store (DB-backed path semantics + versions).

Fixed paths (P2): USER.md / MEMORY.md / memory/session-{id}.md
Mounted only into Supervisor routing view.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from ...utils.logger import get_logger
from .budget import apply_memory_budget

logger = get_logger("cpa-agent.agent_runtime.memory")

FIXED_PATHS = frozenset({"USER.md", "MEMORY.md"})
SESSION_PATH_PREFIX = "memory/session-"


def session_memory_path(session_id: str) -> str:
    return f"{SESSION_PATH_PREFIX}{session_id}.md"


def _owner_student_id(owner: Any) -> str:
    """Accept either a raw student_id or a RequestScope-like owner object."""
    if isinstance(owner, str):
        sid = owner
    else:
        sid = getattr(owner, "student_id", None)
        if sid is None and isinstance(owner, dict):
            sid = owner.get("student_id") or owner.get("studentId")
    sid = str(sid or "").strip()
    if not sid:
        raise PermissionError("student_id owner required for memory store")
    return sid


class MemoryStoreService:
    """MySQL-backed memory store scoped by student_id."""

    def __init__(self, mysql_client=None):
        self._mysql = mysql_client

    @property
    def mysql(self):
        if self._mysql is None:
            from ..db import get_mysql_client
            self._mysql = get_mysql_client()
        return self._mysql

    def ensure_store(self, student_id: Any) -> str:
        student_id = _owner_student_id(student_id)
        store_id = f"student:{student_id}"
        try:
            self.mysql.execute(
                """
                INSERT INTO memory_store (store_id, student_id, status, created_at, updated_at)
                VALUES (:store_id, :student_id, 'active', :now, :now)
                ON DUPLICATE KEY UPDATE updated_at = VALUES(updated_at), status = 'active'
                """,
                {
                    "store_id": store_id,
                    "student_id": student_id,
                    "now": datetime.now().isoformat(sep=" "),
                },
                commit=True,
            )
        except Exception as e:
            logger.warning("ensure_store failed: %s", e)
        return store_id

    def get_entry(self, student_id: Any, path: str) -> Optional[Dict[str, Any]]:
        student_id = _owner_student_id(student_id)
        store_id = self.ensure_store(student_id)
        try:
            row = self.mysql.fetch_one(
                """
                SELECT entry_id, store_id, path, body_md, version, updated_at
                FROM memory_entry
                WHERE store_id = :store_id AND path = :path AND is_current = 1
                LIMIT 1
                """,
                {"store_id": store_id, "path": path},
            )
            return dict(row) if row else None
        except Exception as e:
            logger.warning("get_entry failed: %s", e)
            return None

    def write_entry(
        self,
        student_id: Any,
        path: str,
        body_md: str,
        *,
        source: str = "system",
    ) -> Dict[str, Any]:
        student_id = _owner_student_id(student_id)
        store_id = self.ensure_store(student_id)
        now = datetime.now().isoformat(sep=" ")
        entry_id = f"mem_{uuid.uuid4().hex[:12]}"

        try:
            prev = self.get_entry(student_id, path)
            next_version = int(prev["version"]) + 1 if prev else 1

            if prev:
                self.mysql.execute(
                    """
                    UPDATE memory_entry SET is_current = 0
                    WHERE store_id = :store_id AND path = :path AND is_current = 1
                    """,
                    {"store_id": store_id, "path": path},
                    commit=True,
                )
                # snapshot previous for rollback
                self.mysql.execute(
                    """
                    INSERT INTO memory_entry_version
                    (entry_id, store_id, path, body_md, version, source, created_at)
                    VALUES (:entry_id, :store_id, :path, :body_md, :version, :source, :now)
                    """,
                    {
                        "entry_id": prev.get("entry_id") or entry_id,
                        "store_id": store_id,
                        "path": path,
                        "body_md": prev.get("body_md") or "",
                        "version": prev.get("version") or 0,
                        "source": "snapshot",
                        "now": now,
                    },
                    commit=True,
                )

            self.mysql.execute(
                """
                INSERT INTO memory_entry
                (entry_id, store_id, path, body_md, version, is_current, source, created_at, updated_at)
                VALUES
                (:entry_id, :store_id, :path, :body_md, :version, 1, :source, :now, :now)
                """,
                {
                    "entry_id": entry_id,
                    "store_id": store_id,
                    "path": path,
                    "body_md": body_md or "",
                    "version": next_version,
                    "source": source,
                    "now": now,
                },
                commit=True,
            )
            return {
                "entry_id": entry_id,
                "store_id": store_id,
                "path": path,
                "version": next_version,
                "body_md": body_md or "",
            }
        except Exception as e:
            logger.warning("write_entry failed: %s", e)
            return {"error": str(e), "path": path}

    def rollback(self, student_id: Any, path: str) -> Optional[Dict[str, Any]]:
        """Restore previous version snapshot if available."""
        student_id = _owner_student_id(student_id)
        store_id = self.ensure_store(student_id)
        try:
            snap = self.mysql.fetch_one(
                """
                SELECT body_md, version FROM memory_entry_version
                WHERE store_id = :store_id AND path = :path
                ORDER BY version DESC LIMIT 1
                """,
                {"store_id": store_id, "path": path},
            )
            if not snap:
                return None
            return self.write_entry(
                student_id, path, snap["body_md"] or "", source="rollback",
            )
        except Exception as e:
            logger.warning("rollback failed: %s", e)
            return None

    def mount_for_supervisor(self, student_id: Any) -> str:
        if not student_id:
            return ""
        student_id = _owner_student_id(student_id)
        user = self.get_entry(student_id, "USER.md")
        memory = self.get_entry(student_id, "MEMORY.md")
        return apply_memory_budget(
            (user or {}).get("body_md") or "",
            (memory or {}).get("body_md") or "",
        )

    def export_markdown(self, student_id: Any) -> Dict[str, str]:
        student_id = _owner_student_id(student_id)
        out = {}
        for path in ("USER.md", "MEMORY.md"):
            entry = self.get_entry(student_id, path)
            out[path] = (entry or {}).get("body_md") or ""
        return out

    def write_user_constraints(self, student_id: Any, constraints_text: str) -> Dict[str, Any]:
        """Immediate USER.md update on explicit user confirmation."""
        student_id = _owner_student_id(student_id)
        existing = self.get_entry(student_id, "USER.md")
        prev = (existing or {}).get("body_md") or ""
        block = f"\n## Confirmed constraints\n{constraints_text.strip()}\n"
        if constraints_text.strip() in prev:
            body = prev
        else:
            body = (prev.rstrip() + "\n" + block).strip() + "\n"
        return self.write_entry(student_id, "USER.md", body, source="user_confirm")

    def reflect_session(
        self,
        student_id: Any,
        session_id: str,
        conversation_summary: str,
        *,
        promote: bool = True,
    ) -> Dict[str, Any]:
        """
        Session-end reflection: write session STM file; optionally promote to MEMORY.md.
        Correct/rollback: if promote content empty, skip; keep version snapshot for rollback.
        """
        student_id = _owner_student_id(student_id)
        stm_path = session_memory_path(session_id)
        stm_body = f"# Session {session_id}\n\n{conversation_summary or '(empty)'}\n"
        stm = self.write_entry(student_id, stm_path, stm_body, source="reflection_stm")

        result: Dict[str, Any] = {"stm": stm, "promoted": False}
        if not promote or not (conversation_summary or "").strip():
            return result

        # promote: append a dated bullet to MEMORY.md
        mem = self.get_entry(student_id, "MEMORY.md")
        prev = (mem or {}).get("body_md") or "# Long-term memory\n"
        stamp = datetime.now().strftime("%Y-%m-%d")
        snippet = conversation_summary.strip()
        if len(snippet) > 500:
            snippet = snippet[:497] + "..."
        addition = f"\n## {stamp} (session {session_id})\n- {snippet}\n"
        # simple correct: avoid duplicate session id promote
        if session_id in prev:
            result["promoted"] = False
            result["reason"] = "already_promoted"
            return result
        new_body = prev.rstrip() + "\n" + addition
        written = self.write_entry(student_id, "MEMORY.md", new_body, source="reflection_promote")
        result["memory"] = written
        result["promoted"] = "error" not in written
        return result


_memory_service: Optional[MemoryStoreService] = None


def get_memory_store() -> MemoryStoreService:
    global _memory_service
    if _memory_service is None:
        _memory_service = MemoryStoreService()
    return _memory_service
