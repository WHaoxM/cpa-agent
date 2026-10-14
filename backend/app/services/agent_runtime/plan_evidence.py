"""Domain reference verification for Experts; tool invocation != business completion."""
import json

from ..job_publication import content_sha256


def _json(value):
    return json.loads(value) if isinstance(value, str) else value or {}


def verify_plan_updates(mysql, student_id, packet, out):
    """Only this invocation's trusted pipeline task artifacts may nominate rows.

    Neither model blackboard JSON nor prose, IDs in old session state, or tool
    names alone confer completion. SQL queries are always scoped to server owner.
    """
    if packet.subagent == "planner":
        saved = out.get("persisted_plan") if isinstance(out, dict) else None
        if (not student_id or out.get("incomplete") or "plan.current" not in packet.write_keys
                or not isinstance(saved, dict)):
            return {}
        plan_id = str(saved.get("plan_id") or "")
        artifact_id = str(saved.get("artifact_id") or "")
        digest = str(saved.get("body_sha256") or "")
        session_id = str(saved.get("session_id") or "")
        if not all((plan_id, artifact_id, digest, session_id)):
            return {}
        rows = mysql.fetch_all(
            """
            SELECT p.plan_id, p.body_sha256, p.session_id, a.artifact_id, a.status
            FROM agent_action_plan p JOIN agent_artifact a
              ON a.resource_type = 'agent_action_plan' AND a.resource_id = p.plan_id
            WHERE p.plan_id = :plan_id AND p.student_id = :student_id
              AND p.session_id = :session_id AND a.student_id = :student_id
              AND a.artifact_id = :artifact_id LIMIT 1
            """,
            {"plan_id": plan_id, "artifact_id": artifact_id,
             "student_id": student_id, "session_id": session_id},
        )
        if not rows or rows[0].get("body_sha256") != digest or rows[0].get("status") != "completed":
            return {}
        return {"plan.current": {
            "plan_id": plan_id, "resource_id": plan_id, "artifact_id": artifact_id,
            "status": "completed", "persisted": True,
            "summary": str(saved.get("summary") or "")[:160],
        }}

    contracts = {
        "profile": ("L2_generate_portrait", "portrait.current", "portrait_id"),
        "match": ("L3_run_match", "match.current", "match_id"),
        "report": ("L4_generate_report", "report.current", "report_id"),
    }
    contract = contracts.get(packet.subagent)
    if not contract or not student_id or out.get("incomplete"):
        return {}
    level, key, id_key = contract
    if key not in packet.write_keys:
        return {}
    for call in out.get("tool_calls") or []:
        if not isinstance(call, dict) or call.get("tool_name") != "pipeline_run":
            continue
        artifacts = list(call.get("artifact_candidates") or []) + list(call.get("artifact_refs") or [])
        for artifact in artifacts:
            if not isinstance(artifact, dict) or (artifact.get("resource_type") or artifact.get("kind")) != "pipeline_task":
                continue
            preview = artifact.get("preview") or {}
            tid = artifact.get("resource_id") or (preview.get("task_id") if isinstance(preview, dict) else None)
            if not isinstance(tid, str) or not tid:
                continue
            tasks = mysql.fetch_all(
                "SELECT task_id,student_id,pipeline,status,result_json FROM pipeline_task "
                "WHERE task_id=:tid AND student_id=:sid LIMIT 1", {"tid": tid, "sid": student_id})
            if not tasks:
                continue
            task = tasks[0]
            if (task.get("student_id") != student_id or task.get("task_id") != tid
                    or task.get("pipeline") != level or task.get("status") != "completed"):
                continue
            result = _json(task.get("result_json"))
            rid = result.get(id_key) if isinstance(result, dict) else None
            if not rid:
                continue
            if packet.subagent == "profile":
                rows = mysql.fetch_all("SELECT portrait_json FROM student_portrait_4d WHERE id=:rid AND student_id=:sid",
                                       {"rid": rid, "sid": student_id})
                value = _json(rows[0].get("portrait_json")) if rows else None
                valid = (isinstance(value, dict) and value == result.get("portrait")
                         and value.get("source_portrait_task_id") == tid)
                if valid:
                    from ..school_tier_review import portrait_review_current
                    valid = portrait_review_current(
                        mysql, student_id=student_id,
                        confirmation_task_id=value.get("source_confirmation_task_id"),
                        portrait=value)
            elif packet.subagent == "match":
                rows = mysql.fetch_all("SELECT result_json FROM match_result WHERE id=:rid AND student_id=:sid",
                                       {"rid": rid, "sid": student_id})
                value = _json(rows[0].get("result_json")) if rows else None
                valid = (isinstance(value, dict) and value.get("source_match_task_id") == tid
                         and result.get("verified") is True and result.get("match_sha256") == content_sha256(value))
            else:
                rows = mysql.fetch_all("SELECT status,report_json FROM career_report WHERE report_id=:rid AND student_id=:sid",
                                       {"rid": rid, "sid": student_id})
                value = _json(rows[0].get("report_json")) if rows else None
                valid = (rows and rows[0].get("status") == "completed" and isinstance(value, dict)
                         and value.get("report_id") == rid and value.get("student_id") == student_id
                         and result.get("verified") is True and value.get("sources") == result.get("sources"))
            if valid:
                return {key: {id_key: str(rid), "resource_id": str(rid), "source_task_id": tid,
                              "status": "completed", "verified": True,
                              "summary": str(result.get("summary") or "")}}
    return {}
