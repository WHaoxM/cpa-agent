"""Owner-scoped action plans with an atomic Agent artifact index entry."""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import text

from .artifact_committer import ArtifactCommitter
from .reply_egress import sanitize_user_reply
from .turn_contract import normalize_artifact_ref


class ActionPlanStore:
    def __init__(self, mysql=None):
        self._mysql = mysql
        self._schema_ready = False

    @property
    def mysql(self):
        if self._mysql is None:
            from ..db import get_mysql_client

            self._mysql = get_mysql_client()
        return self._mysql

    def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        self.mysql.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_action_plan (
                plan_id         VARCHAR(64) NOT NULL PRIMARY KEY,
                student_id      VARCHAR(64) NOT NULL,
                session_id      VARCHAR(64) NOT NULL,
                title           VARCHAR(160) NOT NULL,
                body_text       MEDIUMTEXT NOT NULL,
                body_sha256     CHAR(64) NOT NULL,
                status          VARCHAR(32) NOT NULL DEFAULT 'draft',
                created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_plan_owner_session (student_id, session_id, created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """,
            commit=True,
        )
        ArtifactCommitter(self.mysql)._ensure_schema()
        self._schema_ready = True

    def create_from_reply(
        self,
        reply: str,
        *,
        student_id: Optional[str],
        session_id: Optional[str],
    ) -> Dict[str, Any]:
        if not student_id or not session_id:
            raise ValueError("保存学习计划需要已绑定的用户和会话")
        body = sanitize_user_reply(reply, max_len=None).strip()
        if len(body) < 40:
            raise ValueError("本轮尚未形成可保存的学习计划")
        if len(body) > 8000:
            raise ValueError("学习计划超过 8000 字，请缩短后重试；未保存截断版本")
        lead = body[:600]
        if (re.search(r"(?:无法|不能|尚不能|没法).{0,32}(?:制定|生成|启动).{0,20}(?:学习)?(?:计划|规划)", lead)
                and re.search(r"请(?:先|补充|提供)|需要至少", lead)):
            raise ValueError("本轮回复仍在索要前置信息，未形成可保存的计划")
        self._ensure_schema()
        plan_id = f"plan_{uuid.uuid4().hex[:20]}"
        artifact_id = f"art_{uuid.uuid4().hex[:20]}"
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        title = "学习计划（初稿）"
        summary = body.replace("\n", " ")[:160]
        now = datetime.now()
        # One transaction: the domain body and the public artifact pointer must
        # become visible together, or neither may be advertised as completed.
        with self.mysql.engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO agent_action_plan
                (plan_id, student_id, session_id, title, body_text, body_sha256, status, created_at, updated_at)
                VALUES (:plan_id, :student_id, :session_id, :title, :body_text, :body_sha256,
                        'draft', :created_at, :updated_at)
            """), {
                "plan_id": plan_id, "student_id": student_id, "session_id": session_id,
                "title": title, "body_text": body, "body_sha256": digest,
                "created_at": now, "updated_at": now,
            })
            conn.execute(text("""
                INSERT INTO agent_artifact
                (artifact_id, student_id, session_id, kind, status, title, summary,
                 preview_json, actions_json, resource_type, resource_id, version, created_at, updated_at)
                VALUES (:artifact_id, :student_id, :session_id, 'action_plan', 'completed',
                        :title, :summary, :preview_json, :actions_json,
                        'agent_action_plan', :plan_id, 1, :created_at, :updated_at)
            """), {
                "artifact_id": artifact_id, "student_id": student_id, "session_id": session_id,
                "title": title, "summary": summary, "preview_json": "{}",
                "actions_json": '[{"id":"continue_plan","label":"打开学习计划"}]',
                "plan_id": plan_id, "created_at": now, "updated_at": now,
            })
            row = conn.execute(text("""
                SELECT p.plan_id, p.body_sha256, a.artifact_id, a.status
                FROM agent_action_plan p JOIN agent_artifact a
                  ON a.resource_type = 'agent_action_plan' AND a.resource_id = p.plan_id
                WHERE p.plan_id = :plan_id AND p.student_id = :student_id
                  AND p.session_id = :session_id AND a.student_id = :student_id
                  AND a.artifact_id = :artifact_id
            """), {
                "plan_id": plan_id, "artifact_id": artifact_id,
                "student_id": student_id, "session_id": session_id,
            }).mappings().first()
            if not row or row["body_sha256"] != digest or row["status"] != "completed":
                raise RuntimeError("学习计划写入后核验失败")

        ref = normalize_artifact_ref({
            "artifact_id": artifact_id, "kind": "action_plan", "status": "completed",
            "title": title, "summary": summary,
            "actions": [{"id": "continue_plan", "label": "打开学习计划"}],
        })
        if not ref:
            raise RuntimeError("学习计划成果引用无效")
        return {
            "plan_id": plan_id, "artifact_id": artifact_id, "session_id": session_id,
            "body_sha256": digest, "summary": summary, "artifact": ref,
        }

    def get_for_owner(
        self,
        plan_id: str,
        *,
        student_id: Optional[str],
        session_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if not plan_id or not student_id:
            return None
        self._ensure_schema()
        row = self.mysql.fetch_one(
            """
            SELECT plan_id, student_id, session_id, title, body_text, body_sha256,
                   status, created_at, updated_at
            FROM agent_action_plan
            WHERE plan_id = :plan_id AND student_id = :student_id
            LIMIT 1
            """,
            {"plan_id": plan_id, "student_id": student_id},
        )
        if not row or (session_id and row.get("session_id") != session_id):
            return None
        body = str(row.get("body_text") or "")
        if hashlib.sha256(body.encode("utf-8")).hexdigest() != row.get("body_sha256"):
            return None
        return {
            "plan_id": row["plan_id"], "session_id": row["session_id"],
            "title": row["title"], "body": body, "status": row["status"],
            "created_at": str(row.get("created_at") or ""),
            "updated_at": str(row.get("updated_at") or ""),
        }


_store: Optional[ActionPlanStore] = None


def get_action_plan_store() -> ActionPlanStore:
    global _store
    if _store is None:
        _store = ActionPlanStore()
    return _store
