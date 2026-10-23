"""Owner-scoped action plans with an atomic Agent artifact index entry."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import text

from .artifact_committer import ArtifactCommitter
from .reply_egress import sanitize_user_reply
from .turn_contract import normalize_artifact_ref


class ActionPlanStore:
    def __init__(self, mysql=None, *, schema_ready=False):
        self._mysql = mysql
        # Explicitly bound transaction readers must not perform runtime DDL.
        # Callers use this only after migration/schema preparation.
        self._schema_ready = schema_ready

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
                handoff_id      VARCHAR(64) NULL,
                report_uri      VARCHAR(255) NULL,
                report_version  VARCHAR(128) NULL,
                resume_version  VARCHAR(128) NULL,
                gap_summary     MEDIUMTEXT NULL,
                exploration_round_id VARCHAR(64) NULL,
                created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_plan_owner_session (student_id, session_id, created_at),
                UNIQUE KEY uq_action_plan_owner_handoff (student_id, handoff_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """,
            commit=True,
        )
        self.mysql.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_action_plan_step (
                step_id VARCHAR(64) NOT NULL PRIMARY KEY,
                plan_id VARCHAR(64) NOT NULL,
                position SMALLINT UNSIGNED NOT NULL,
                title VARCHAR(160) NOT NULL,
                detail VARCHAR(600) NOT NULL,
                completed TINYINT(1) NOT NULL DEFAULT 0,
                completed_at DATETIME NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY uq_action_plan_step_position (plan_id, position),
                INDEX idx_action_plan_step_plan (plan_id, position),
                CONSTRAINT fk_action_plan_step_plan FOREIGN KEY (plan_id)
                    REFERENCES agent_action_plan(plan_id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """,
            commit=True,
        )
        self.mysql.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_action_plan_step_evidence (
                step_id VARCHAR(64) NOT NULL PRIMARY KEY,
                plan_id VARCHAR(64) NOT NULL,
                student_id VARCHAR(64) NOT NULL,
                summary MEDIUMTEXT NOT NULL,
                attachment_file_id VARCHAR(64) NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                INDEX idx_action_plan_evidence_owner_plan (student_id, plan_id),
                CONSTRAINT fk_action_plan_evidence_plan FOREIGN KEY (plan_id)
                    REFERENCES agent_action_plan(plan_id) ON DELETE CASCADE,
                CONSTRAINT fk_action_plan_evidence_step FOREIGN KEY (step_id)
                    REFERENCES agent_action_plan_step(step_id) ON DELETE CASCADE
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

    def create_from_steps(
        self,
        steps: list[dict[str, str]],
        *,
        student_id: Optional[str],
        session_id: Optional[str],
        handoff_id: str,
        report_uri: str,
        report_version: str,
        resume_version: str,
        gap_summary: str,
    ) -> Dict[str, Any]:
        """Commit a report-linked one-week plan, its steps and artifact together."""
        if not student_id or not session_id:
            raise ValueError("保存学习计划需要已绑定的用户和会话")
        if not isinstance(handoff_id, str) or not handoff_id.startswith("aph_"):
            raise ValueError("行动计划交接无效")
        if not report_uri.startswith("career://me/reports/") or not report_version or not resume_version:
            raise ValueError("岗位报告或已确认简历来源无效")
        return self._create_structured_steps(
            steps, student_id=student_id, session_id=session_id, handoff_id=handoff_id,
            report_uri=report_uri, report_version=report_version,
            resume_version=resume_version, gap_summary=gap_summary,
        )

    def create_exploration_steps(self, steps, *, student_id, session_id, round_id):
        """A separate source adapter: never weaken the report-linked entry point."""
        from ..career_exploration import ExplorationService
        service = ExplorationService(self.mysql)
        row = service.round_for_owner(round_id, student_id)
        if row['session_id'] != session_id:
            raise ValueError('探索会话不一致')
        service.require_current_sources(row, student_id)
        selection = json.loads(row['selection_json']) if isinstance(row['selection_json'], str) else row['selection_json']
        return self._create_structured_steps(
            steps, student_id=student_id, session_id=session_id,
            handoff_id='aph_' + round_id, report_uri='', report_version='',
            resume_version='', gap_summary=selection['question'], exploration_round_id=round_id,
        )

    def _create_structured_steps(self, steps, *, student_id, session_id, handoff_id,
                                 report_uri, report_version, resume_version, gap_summary,
                                 exploration_round_id=None):
        normalized = []
        for item in steps if isinstance(steps, list) else []:
            if not isinstance(item, dict):
                continue
            title = sanitize_user_reply(str(item.get("title") or ""), max_len=160).strip()
            detail = sanitize_user_reply(str(item.get("detail") or ""), max_len=600).strip()
            if title and detail:
                normalized.append({"title": title, "detail": detail})
        if not 3 <= len(normalized) <= 5:
            raise ValueError("一周行动计划需要 3 至 5 个具体步骤")

        self._ensure_schema()
        existing = self._find_handoff(handoff_id, student_id=student_id, session_id=session_id)
        if existing:
            return existing
        plan_id = f"plan_{uuid.uuid4().hex[:20]}"
        artifact_id = f"art_{uuid.uuid4().hex[:20]}"
        title = "一周行动计划"
        body = "\n".join(
            f"{index + 1}. {step['title']}\n   {step['detail']}"
            for index, step in enumerate(normalized)
        )
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        summary = "；".join(step["title"] for step in normalized)[:160]
        now = datetime.now()
        with self.mysql.engine.begin() as conn:
            if exploration_round_id:
                bound = conn.execute(text('''
                    SELECT r.plan_id,r.closed_reason FROM career_exploration_round r
                    JOIN career_exploration e ON e.exploration_id=r.exploration_id
                    WHERE r.round_id=:rid AND e.student_id=:sid AND e.session_id=:session
                    FOR UPDATE
                '''), {'rid': exploration_round_id, 'sid': student_id, 'session': session_id}).mappings().first()
                if not bound:
                    raise ValueError('探索轮次不存在')
                if bound['plan_id']:
                    from ..career_exploration import ExplorationService
                    reader = ExplorationService.in_transaction(conn).mysql
                    return ActionPlanStore(reader, schema_ready=True).get_for_owner(bound['plan_id'], student_id=student_id)
                if bound['closed_reason']:
                    raise ValueError('这一轮已明确重新准备，迟到的计划不再保存')
                conn.execute(text('SELECT student_id FROM student_profile WHERE student_id=:sid FOR UPDATE'),
                             {'sid': student_id})
                from ..career_exploration import ExplorationService
                service = ExplorationService.in_transaction(conn)
                service.require_current_sources(service.round_for_owner(exploration_round_id, student_id), student_id)
            # A unique owner+handoff key makes client/network retries idempotent.
            prior = conn.execute(text("""
                SELECT p.plan_id, a.artifact_id
                FROM agent_action_plan p JOIN agent_artifact a
                  ON a.resource_type = 'agent_action_plan' AND a.resource_id = p.plan_id
                WHERE p.student_id = :student_id AND p.session_id = :session_id
                  AND p.handoff_id = :handoff_id
                LIMIT 1
            """), {"student_id": student_id, "session_id": session_id,
                   "handoff_id": handoff_id}).mappings().first()
            if prior:
                existing_plan = self.get_for_owner(
                    str(prior["plan_id"]), student_id=student_id, session_id=session_id,
                )
                if existing_plan:
                    return existing_plan
                raise RuntimeError("既有行动计划无法核验")
            conn.execute(text("""
                INSERT INTO agent_action_plan
                (plan_id, student_id, session_id, title, body_text, body_sha256, status,
                 handoff_id, report_uri, report_version, resume_version, gap_summary,
                 exploration_round_id,
                 created_at, updated_at)
                VALUES (:plan_id, :student_id, :session_id, :title, :body, :digest,
                        'not_started', :handoff_id, :report_uri, :report_version,
                        :resume_version, :gap_summary, :exploration_round_id, :created_at, :updated_at)
            """), {
                "plan_id": plan_id, "student_id": student_id, "session_id": session_id,
                "title": title, "body": body, "digest": digest, "handoff_id": handoff_id,
                "report_uri": report_uri, "report_version": report_version,
                "resume_version": resume_version,
                "gap_summary": sanitize_user_reply(gap_summary, max_len=1200),
                "exploration_round_id": exploration_round_id,
                "created_at": now, "updated_at": now,
            })
            for index, step in enumerate(normalized, start=1):
                conn.execute(text("""
                    INSERT INTO agent_action_plan_step
                    (step_id, plan_id, position, title, detail, completed, completed_at, created_at)
                    VALUES (:step_id, :plan_id, :position, :title, :detail, 0, NULL, :created_at)
                """), {
                    "step_id": f"step_{uuid.uuid4().hex[:20]}", "plan_id": plan_id,
                    "position": index, "title": step["title"], "detail": step["detail"],
                    "created_at": now,
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
                "title": title, "summary": summary,
                "preview_json": json_dumps({"type": "plan_preview", "steps": len(normalized),
                                            "completed": 0, "duration": "一周"}),
                "actions_json": '[{"id":"continue_plan","label":"打开学习计划"}]',
                "plan_id": plan_id, "created_at": now, "updated_at": now,
            })
            if exploration_round_id:
                conn.execute(text('UPDATE career_exploration_round SET plan_id=:pid WHERE round_id=:rid'),
                             {'pid': plan_id, 'rid': exploration_round_id})
                conn.execute(text('''UPDATE career_exploration e JOIN career_exploration_round r
                    ON r.exploration_id=e.exploration_id SET e.revision=e.revision+1,e.updated_at=NOW()
                    WHERE r.round_id=:rid'''), {'rid': exploration_round_id})

        result = self._find_handoff(handoff_id, student_id=student_id, session_id=session_id)
        if not result or result.get("body_sha256") != digest or len(result.get("steps") or []) != len(normalized):
            raise RuntimeError("行动计划写入后核验失败")
        return result

    def _find_handoff(self, handoff_id: str, *, student_id: str, session_id: str) -> Optional[Dict[str, Any]]:
        row = self.mysql.fetch_one("""
            SELECT plan_id FROM agent_action_plan
            WHERE handoff_id = :handoff_id AND student_id = :student_id AND session_id = :session_id
            LIMIT 1
        """, {"handoff_id": handoff_id, "student_id": student_id, "session_id": session_id})
        if not row:
            return None
        return self.get_for_owner(str(row["plan_id"]), student_id=student_id, session_id=session_id)

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
            SELECT p.plan_id, p.student_id, p.session_id, p.title, p.body_text, p.body_sha256,
                   p.status, p.handoff_id, p.report_uri, p.report_version, p.resume_version,
                   p.gap_summary, p.exploration_round_id, p.created_at, p.updated_at,
                   a.artifact_id, a.status AS artifact_status, a.summary AS artifact_summary
            FROM agent_action_plan p
            LEFT JOIN agent_artifact a ON a.resource_type = 'agent_action_plan'
              AND a.resource_id = p.plan_id AND a.student_id = p.student_id
            WHERE p.plan_id = :plan_id AND p.student_id = :student_id
            LIMIT 1
            """,
            {"plan_id": plan_id, "student_id": student_id},
        )
        if not row or (session_id and row.get("session_id") != session_id):
            return None
        body = str(row.get("body_text") or "")
        if hashlib.sha256(body.encode("utf-8")).hexdigest() != row.get("body_sha256"):
            return None
        steps = self.mysql.fetch_all("""
            SELECT step_id, position, title, detail, completed, completed_at
            FROM agent_action_plan_step WHERE plan_id = :plan_id ORDER BY position ASC
        """, {"plan_id": plan_id})
        evidence_rows = self.mysql.fetch_all("""
            SELECT step_id, summary, attachment_file_id, created_at, updated_at
            FROM agent_action_plan_step_evidence
            WHERE plan_id = :plan_id AND student_id = :student_id
        """, {"plan_id": plan_id, "student_id": student_id})
        evidence_by_step = {str(item["step_id"]): item for item in evidence_rows}
        steps = [{
            "step_id": str(step["step_id"]), "position": int(step["position"]),
            "title": str(step["title"]), "detail": str(step["detail"]),
            "completed": bool(step["completed"]),
            "completed_at": str(step.get("completed_at") or "") or None,
            "evidence": _project_step_evidence(evidence_by_step.get(str(step["step_id"]))),
        } for step in steps]
        total = len(steps)
        completed = sum(1 for step in steps if step["completed"])
        progress_status = (
            "not_started" if not completed else "completed" if completed == total else "in_progress"
        ) if total else str(row["status"])
        source_status = None
        if row.get("report_uri"):
            source_status = _action_plan_source_status(
                self.mysql, student_id=student_id, report_uri=str(row["report_uri"]),
                report_version=str(row.get("report_version") or ""),
                resume_version=str(row.get("resume_version") or ""),
            )
        source = {"label": "岗位报告与已确认简历", "status": source_status} if source_status else None
        if row.get('exploration_round_id'):
            from ..career_exploration import ExplorationService
            source = ExplorationService(self.mysql).plan_source(row['exploration_round_id'], student_id)
        artifact = normalize_artifact_ref({
            "artifact_id": row.get("artifact_id"),
            "kind": "action_plan",
            "status": row.get("artifact_status") or "completed",
            "title": row.get("title") or "一周行动计划",
            "summary": row.get("artifact_summary") or "",
            "preview": {"type": "plan_preview", "steps": total,
                        "completed": completed, "duration": "一周"},
            "actions": [{"id": "continue_plan", "label": "打开学习计划"}],
        }) if row.get("artifact_id") else None
        return {
            "plan_id": row["plan_id"], "student_id": student_id,
            "session_id": row["session_id"],
            "artifact_id": row.get("artifact_id"),
            "title": row["title"], "body": body, "status": progress_status,
            "steps": steps, "progress": {"completed": completed, "total": total,
                                            "status": progress_status},
            "source": source,
            "artifact": artifact,
            "body_sha256": row["body_sha256"],
            "created_at": str(row.get("created_at") or ""),
            "updated_at": str(row.get("updated_at") or ""),
        }

    def set_step_completed(
        self, plan_id: str, step_id: str, completed: bool, *, student_id: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        if not plan_id or not step_id or not student_id or type(completed) is not bool:
            return None
        self._ensure_schema()
        now = datetime.now()
        with self.mysql.engine.begin() as conn:
            row = conn.execute(text("""
                SELECT s.completed FROM agent_action_plan_step s
                JOIN agent_action_plan p ON p.plan_id = s.plan_id
                WHERE p.plan_id = :plan_id AND s.step_id = :step_id
                  AND p.student_id = :student_id
                LIMIT 1
            """), {"plan_id": plan_id, "step_id": step_id, "student_id": student_id}).mappings().first()
            if not row:
                return None
            if bool(row["completed"]) != completed:
                conn.execute(text("""
                    UPDATE agent_action_plan_step SET completed = :completed,
                        completed_at = :completed_at
                    WHERE step_id = :step_id AND plan_id = :plan_id
                """), {"completed": 1 if completed else 0,
                       "completed_at": now if completed else None,
                       "step_id": step_id, "plan_id": plan_id})
            totals = conn.execute(text("""
                SELECT COUNT(*) AS total, SUM(completed) AS completed
                FROM agent_action_plan_step WHERE plan_id = :plan_id
            """), {"plan_id": plan_id}).mappings().first()
            total = int(totals["total"] or 0)
            done = int(totals["completed"] or 0)
            status = "not_started" if done == 0 else "completed" if done == total else "in_progress"
            conn.execute(text("""
                UPDATE agent_action_plan SET status = :status, updated_at = :updated_at
                WHERE plan_id = :plan_id AND student_id = :student_id
            """), {"status": status, "updated_at": now,
                   "plan_id": plan_id, "student_id": student_id})
        return self.get_for_owner(plan_id, student_id=student_id)

    def save_step_evidence(
        self,
        plan_id: str,
        step_id: str,
        summary: str,
        attachment_file_id: Optional[str],
        *,
        student_id: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        """Create/update one user-submitted record without touching plan progress."""
        if (not plan_id or not step_id or not student_id or not isinstance(summary, str)
                or not 1 <= len(summary.strip()) <= 2000
                or (attachment_file_id is not None and not isinstance(attachment_file_id, str))):
            return None
        cleaned = sanitize_user_reply(summary.strip(), max_len=2000).strip()
        if not cleaned or len(cleaned) > 2000:
            return None
        self._ensure_schema()
        now = datetime.now()
        with self.mysql.engine.begin() as conn:
            lock = " FOR UPDATE" if conn.dialect.name == "mysql" else ""
            row = conn.execute(text("""
                SELECT e.summary, e.attachment_file_id, s.step_id AS owned_step_id
                FROM agent_action_plan_step s
                JOIN agent_action_plan p ON p.plan_id = s.plan_id
                LEFT JOIN agent_action_plan_step_evidence e
                  ON e.step_id = s.step_id AND e.plan_id = p.plan_id
                WHERE p.plan_id = :plan_id AND p.student_id = :student_id
                  AND s.step_id = :step_id
            """ + lock), {"plan_id": plan_id, "student_id": student_id, "step_id": step_id}).mappings().first()
            if not row:
                return None
            if (row["summary"] == cleaned
                    and row["attachment_file_id"] == attachment_file_id):
                pass
            elif row["summary"] is None:
                conn.execute(text("""
                    INSERT INTO agent_action_plan_step_evidence
                    (step_id, plan_id, student_id, summary, attachment_file_id, created_at, updated_at)
                    VALUES (:step_id, :plan_id, :student_id, :summary, :attachment_file_id, :now, :now)
                """), {
                    "step_id": step_id, "plan_id": plan_id, "student_id": student_id,
                    "summary": cleaned, "attachment_file_id": attachment_file_id, "now": now,
                })
            else:
                conn.execute(text("""
                    UPDATE agent_action_plan_step_evidence
                    SET summary = :summary, attachment_file_id = :attachment_file_id, updated_at = :now
                    WHERE step_id = :step_id AND plan_id = :plan_id AND student_id = :student_id
                """), {
                    "step_id": step_id, "plan_id": plan_id, "student_id": student_id,
                    "summary": cleaned, "attachment_file_id": attachment_file_id, "now": now,
                })
                conn.execute(text("""
                    UPDATE agent_action_plan SET updated_at = :now
                    WHERE plan_id = :plan_id AND student_id = :student_id
                """), {"now": now, "plan_id": plan_id, "student_id": student_id})
        return self.get_for_owner(plan_id, student_id=student_id)


def _project_step_evidence(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not row:
        return None
    attachment_file_id = row.get("attachment_file_id")
    attachment = None
    if attachment_file_id:
        attachment = {
            "file_id": str(attachment_file_id),
            "filename": None,
            "ingest_status": "unavailable",
            "read_status": "unavailable",
        }
    return {
        "summary": str(row.get("summary") or ""),
        "source": "student_submitted",
        "verified": False,
        "attachment": attachment,
        "created_at": str(row.get("created_at") or ""),
        "updated_at": str(row.get("updated_at") or ""),
    }


def json_dumps(value: Dict[str, Any]) -> str:
    import json
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _action_plan_source_status(mysql, *, student_id: str, report_uri: str,
                               report_version: str, resume_version: str) -> str:
    """Revalidate stored sources on read, keeping stale plans as history."""
    try:
        from .resource_catalog import read_platform_resource
        from .evidence_retrieval import RESUME_URI

        report = read_platform_resource(mysql, student_id=student_id, uri=report_uri, section="overview")
        resume = read_platform_resource(mysql, student_id=student_id, uri=RESUME_URI, section="overview")
        if (not isinstance(report, dict) or report.get("success") is not True
                or report.get("business_artifact_verified") is not True
                or report.get("status") not in {"completed", "partial"}
                or str(report.get("version") or "") != report_version):
            return "expired"
        if (not isinstance(resume, dict) or resume.get("success") is not True
                or resume.get("status") != "completed"
                or str(resume.get("version") or "") != resume_version):
            return "expired"
        report_data = report.get("data") if isinstance(report.get("data"), dict) else {}
        sources = report_data.get("sources") if isinstance(report_data.get("sources"), dict) else {}
        if str(sources.get("source_confirmation_task_id") or "") != resume_version:
            return "expired"
        return "current"
    except Exception:
        return "unavailable"


_store: Optional[ActionPlanStore] = None


def get_action_plan_store() -> ActionPlanStore:
    global _store
    if _store is None:
        _store = ActionPlanStore()
    return _store
