"""Async tool audit via fork_jobs (never ships to student UI)."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, Optional
from uuid import uuid4

from ...utils.logger import get_logger
from .fork_jobs import enqueue as fork_enqueue, register_handler
from .tool_result_channels import digest_text

logger = get_logger("cpa-agent.agent_runtime.audit")


def enqueue_audit(
    *,
    session_id: Optional[str],
    student_id: Optional[str],
    tool_name: str,
    decision: str,
    args: Optional[Dict[str, Any]] = None,
    result_text: str = "",
    reason: str = "",
) -> str:
    payload = {
        "student_id": student_id,
        "tool_name": tool_name,
        "decision": decision,
        "args_digest": digest_text(json.dumps(args or {}, ensure_ascii=False, default=str)),
        "result_digest": digest_text(result_text or ""),
        "reason": reason[:500],
    }
    return fork_enqueue(session_id or "", "tool_audit", payload)


def _handle_tool_audit(job) -> None:
    p = job.payload or {}
    try:
        from ..db import get_mysql_client
        from .schema_ensure import ensure_tool_audit_schema

        mysql = get_mysql_client()
        ensure_tool_audit_schema(mysql)
        mysql.execute(
            """
            INSERT INTO tool_audit_event
            (event_id, session_id, student_id, tool_name, decision, args_digest, result_digest, reason, created_at)
            VALUES
            (:event_id, :session_id, :student_id, :tool_name, :decision, :args_digest, :result_digest, :reason, :now)
            """,
            {
                "event_id": f"ta_{uuid4().hex[:12]}",
                "session_id": job.session_id or "",
                "student_id": p.get("student_id"),
                "tool_name": p.get("tool_name") or "",
                "decision": p.get("decision") or "",
                "args_digest": p.get("args_digest") or "",
                "result_digest": p.get("result_digest") or "",
                "reason": p.get("reason") or "",
                "now": datetime.now().isoformat(sep=" "),
            },
            commit=True,
        )
    except Exception as e:
        # Fallback: structured log only
        logger.info(
            "tool_audit tool=%s decision=%s session=%s err=%s",
            p.get("tool_name"),
            p.get("decision"),
            job.session_id,
            e,
        )


register_handler("tool_audit", _handle_tool_audit)
