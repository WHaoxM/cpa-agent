"""Owner-scoped durable artifact index for Agent chat surfaces."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from ...utils.logger import get_logger
from .turn_contract import ARTIFACT_KINDS, normalize_artifact_ref, utc_now_iso

logger = get_logger("cpa-agent.agent_runtime.artifact_committer")


class ArtifactCommitter:
    """Commit safe artifact references after a domain/tool result exists."""

    def __init__(self, mysql=None):
        self._mysql = mysql
        self._schema_ready = False

    @property
    def mysql(self):
        if self._mysql is None:
            from ..db import get_mysql_client

            self._mysql = get_mysql_client()
        return self._mysql

    def commit_candidates(
        self,
        candidates: Any,
        *,
        student_id: Optional[str],
        session_id: Optional[str],
        turn_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        if not isinstance(candidates, list) or not candidates:
            return []
        refs: List[Dict[str, Any]] = []
        try:
            self._ensure_schema()
        except Exception as e:
            logger.warning("agent_artifact schema unavailable: %s", e)
            return []

        for candidate in candidates[:8]:
            if not isinstance(candidate, dict):
                continue
            try:
                ref = self._commit_one(
                    candidate,
                    student_id=student_id,
                    session_id=session_id,
                    turn_id=turn_id,
                )
                if ref:
                    refs.append(ref)
            except Exception as e:
                logger.warning("artifact candidate commit skipped: %s", e)
        return refs

    def get_for_owner(self, artifact_id: str, *, student_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not artifact_id or not student_id:
            return None
        self._ensure_schema()
        row = self.mysql.fetch_one(
            """
            SELECT artifact_id, student_id, session_id, turn_id, kind, status, title,
                   summary, preview_json, actions_json, resource_type, resource_id,
                   version, created_at, updated_at
            FROM agent_artifact
            WHERE artifact_id = :artifact_id AND student_id = :student_id
            LIMIT 1
            """,
            {"artifact_id": artifact_id, "student_id": student_id},
        )
        if not row:
            return None
        return self._row_to_public(row)

    def bind_turn(
        self,
        artifact_ids: List[str],
        *,
        student_id: Optional[str],
        session_id: Optional[str],
        turn_id: Optional[str],
    ) -> None:
        if not artifact_ids or not student_id or not turn_id:
            return
        self._ensure_schema()
        for artifact_id in artifact_ids[:8]:
            self.mysql.execute(
                """
                UPDATE agent_artifact
                SET session_id = COALESCE(:session_id, session_id),
                    turn_id = :turn_id,
                    updated_at = :updated_at
                WHERE artifact_id = :artifact_id AND student_id = :student_id
                """,
                {
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "updated_at": datetime.now(),
                    "artifact_id": artifact_id,
                    "student_id": student_id,
                },
                commit=True,
            )

    def _commit_one(
        self,
        candidate: Dict[str, Any],
        *,
        student_id: Optional[str],
        session_id: Optional[str],
        turn_id: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        kind = str(candidate.get("kind") or "file")
        if kind not in ARTIFACT_KINDS:
            return None
        resource_type = str(candidate.get("resource_type") or kind)
        resource_id = str(candidate.get("resource_id") or candidate.get("task_id") or "").strip()
        if not resource_id:
            return None
        artifact_id = str(candidate.get("artifact_id") or f"art_{uuid.uuid4().hex[:16]}")
        # A candidate without an authoritative status is not a completed artifact.
        status = str(candidate.get("status") or "unverified")
        if status == "completed" and candidate.get("verified") is not True:
            status = "unverified"
        title = str(candidate.get("title") or "")
        summary = str(candidate.get("summary") or "")
        preview = candidate.get("preview") if isinstance(candidate.get("preview"), dict) else {}
        actions = candidate.get("actions") if isinstance(candidate.get("actions"), list) else []
        version = int(candidate.get("version") or 1)
        now = datetime.now()
        self.mysql.execute(
            """
            INSERT INTO agent_artifact
            (artifact_id, student_id, session_id, turn_id, kind, status, title, summary,
             preview_json, actions_json, resource_type, resource_id, version, created_at, updated_at)
            VALUES
            (:artifact_id, :student_id, :session_id, :turn_id, :kind, :status, :title, :summary,
             :preview_json, :actions_json, :resource_type, :resource_id, :version, :created_at, :updated_at)
            ON DUPLICATE KEY UPDATE
                status = VALUES(status),
                title = VALUES(title),
                summary = VALUES(summary),
                preview_json = VALUES(preview_json),
                actions_json = VALUES(actions_json),
                resource_type = VALUES(resource_type),
                resource_id = VALUES(resource_id),
                version = VALUES(version),
                updated_at = VALUES(updated_at)
            """,
            {
                "artifact_id": artifact_id,
                "student_id": student_id,
                "session_id": session_id,
                "turn_id": turn_id,
                "kind": kind,
                "status": status,
                "title": title[:120],
                "summary": summary[:500],
                "preview_json": json.dumps(preview, ensure_ascii=False),
                "actions_json": json.dumps(actions, ensure_ascii=False),
                "resource_type": resource_type[:64],
                "resource_id": resource_id[:128],
                "version": version,
                "created_at": now,
                "updated_at": now,
            },
            commit=True,
        )
        return normalize_artifact_ref({
            "artifact_id": artifact_id,
            "kind": kind,
            "status": status,
            "title": title,
            "summary": summary,
            "preview": preview,
            "actions": actions,
            "version": version,
            "created_at": utc_now_iso(),
        })

    def _row_to_public(self, row: Dict[str, Any]) -> Dict[str, Any]:
        preview = _loads_json(row.get("preview_json"), {})
        actions = _loads_json(row.get("actions_json"), [])
        ref = normalize_artifact_ref({
            "artifact_id": row.get("artifact_id"),
            "kind": row.get("kind"),
            "status": row.get("status"),
            "title": row.get("title"),
            "summary": row.get("summary"),
            "preview": preview,
            "actions": actions,
            "version": row.get("version") or 1,
            "created_at": str(row.get("created_at") or ""),
        }) or {}
        return {
            **ref,
            "session_id": row.get("session_id"),
            "turn_id": row.get("turn_id"),
            "resource_type": row.get("resource_type"),
            "resource_id": row.get("resource_id"),
            "updated_at": str(row.get("updated_at") or ""),
        }

    def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        self.mysql.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_artifact (
                id              BIGINT AUTO_INCREMENT PRIMARY KEY,
                artifact_id     VARCHAR(64)  NOT NULL UNIQUE,
                student_id      VARCHAR(64)  NOT NULL,
                session_id      VARCHAR(64)  DEFAULT NULL,
                turn_id         VARCHAR(64)  DEFAULT NULL,
                kind            VARCHAR(32)  NOT NULL,
                status          VARCHAR(32)  NOT NULL DEFAULT 'completed',
                title           VARCHAR(160) NOT NULL,
                summary         VARCHAR(512) DEFAULT NULL,
                preview_json    JSON         DEFAULT NULL,
                actions_json    JSON         DEFAULT NULL,
                resource_type   VARCHAR(64)  NOT NULL,
                resource_id     VARCHAR(128) NOT NULL,
                version         INT          NOT NULL DEFAULT 1,
                created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_owner_kind (student_id, kind, created_at),
                INDEX idx_session_turn (session_id, turn_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='Agent typed artifact index'
            """,
            commit=True,
        )
        self._schema_ready = True


def _loads_json(value: Any, fallback: Any) -> Any:
    if value in (None, ""):
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return fallback


_committer: Optional[ArtifactCommitter] = None


def get_artifact_committer() -> ArtifactCommitter:
    global _committer
    if _committer is None:
        _committer = ArtifactCommitter()
    return _committer
