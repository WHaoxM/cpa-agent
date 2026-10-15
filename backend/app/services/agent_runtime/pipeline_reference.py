"""Resolve page-to-Agent task references against server-owned business state.

The browser supplies only an opaque task id.  Neither a browser title nor a
browser summary is admitted into model context as evidence.
"""

import hashlib
import json
import re


_TASK_ID = re.compile(r"^task_[A-Za-z0-9]+$")
_PIPELINES = {"L0_parse_resume", "L2_generate_portrait", "L3_run_match", "L4_generate_report"}


def _payload(raw):
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return {}
    return raw if isinstance(raw, dict) else {}


def resolve_pipeline_task_ref(mysql, *, student_id: str, task_id: str) -> dict:
    """Fail closed for absent/cross-owner/unsupported/forged completed tasks."""
    if not isinstance(task_id, str) or not _TASK_ID.fullmatch(task_id):
        raise ValueError("无效的任务引用")
    rows = mysql.fetch_all(
        "SELECT task_id, student_id, pipeline, status, result_json FROM pipeline_task "
        "WHERE task_id=:tid AND student_id=:sid LIMIT 1",
        {"tid": task_id, "sid": student_id},
    )
    if not rows:
        raise LookupError("任务引用不存在")
    row = rows[0]
    pipeline, status = row.get("pipeline"), row.get("status")
    if pipeline not in _PIPELINES or status not in {
        "pending", "running", "needs_input", "partial", "failed", "completed"
    }:
        raise ValueError("任务类型或状态不可引用")
    result = _payload(row.get("result_json"))
    if result.get("task_id") not in (None, task_id):
        raise ValueError("任务结果 ID 与权威记录不一致")
    if result.get("student_id") not in (None, student_id):
        raise ValueError("任务结果归属与权威记录不一致")
    if status == "completed":
        if result.get("status") != "completed":
            raise ValueError("任务完成状态未通过结果校验")
        if pipeline == "L2_generate_portrait":
            portrait_id = str(result.get("portrait_id") or "")
            if not portrait_id or str(result.get("resource_id") or "") != portrait_id:
                raise ValueError("画像结果未包含一致的权威 ID")
            portraits = mysql.fetch_all(
                "SELECT id, portrait_json FROM student_portrait_4d "
                "WHERE id=:pid AND student_id=:sid LIMIT 1",
                {"pid": portrait_id, "sid": student_id},
            )
            saved = portraits[0] if portraits else {}
            if str(saved.get("id") or "") != portrait_id or _payload(saved.get("portrait_json")) != result.get("portrait"):
                raise ValueError("画像结果未通过业务记录回读")
            if result.get("snapshot_id"):
                from ..student_portrait_snapshot import read_snapshot_for_task
                snapshot = read_snapshot_for_task(mysql, student_id=student_id,
                                                  source_task_id=task_id)
                if (not snapshot or snapshot["snapshot_id"] != result["snapshot_id"]
                        or snapshot["portrait"] != result["portrait"]):
                    raise ValueError("画像快照未通过业务记录回读")
            from ..school_tier_review import portrait_review_current
            portrait = result["portrait"]
            if not portrait_review_current(
                mysql, student_id=student_id,
                confirmation_task_id=portrait.get("source_confirmation_task_id"),
                portrait=portrait,
            ):
                raise ValueError("学校档位审核已变更或撤回，画像须重新生成")
            from ..pipeline import require_confirmed_profile
            from ..student_self_report import portrait_current
            if not portrait_current(require_confirmed_profile(mysql, student_id), portrait):
                raise ValueError("用户自述已更新，画像须重新生成")
        elif pipeline == "L0_parse_resume":
            if result.get("confirmed") is not True:
                raise ValueError("简历候选尚未得到用户确认")
            from ..pipeline import require_confirmed_profile
            profile = require_confirmed_profile(mysql, student_id)
            if profile.get("_resume_confirmation", {}).get("task_id") != task_id:
                raise ValueError("当前档案确认版本与任务不一致")
        elif pipeline in {"L3_run_match", "L4_generate_report"}:
            # These result ids are checked at their own API before any release.
            # The task alone can be discussed, but cannot prove a business artifact.
            if not result.get("resource_id"):
                raise ValueError("任务没有可验证的业务成果 ID")
    raw = json.dumps(result, ensure_ascii=False, sort_keys=True, default=str)
    verified_business = status == "completed" and pipeline in {"L0_parse_resume", "L2_generate_portrait"}
    ref = {
        "task_id": task_id,
        "pipeline": pipeline,
        "status": status,
        "result_version": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "resource_type": str(result.get("resource_type") or "pipeline_task") if verified_business else "pipeline_task",
        "resource_id": str(result.get("resource_id") or task_id) if verified_business else task_id,
        "business_artifact_verified": verified_business,
        "next_action": str(result.get("next_action") or ""),
    }
    # A completed, owner-verified L0/L2 task has a stable canonical resource
    # family.  Turn preparation still has to compare this task id with the
    # current resource catalog version before it may mount evidence.
    if verified_business and pipeline == "L0_parse_resume":
        ref["canonical_resource_uri"] = "career://me/resume"
    elif verified_business and pipeline == "L2_generate_portrait":
        ref["canonical_resource_uri"] = "career://me/portrait"
    return ref
