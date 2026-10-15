"""Owner-confirmed, explicitly self-reported behavioral observations.

Stored alongside the confirmed profile so a new L0 confirmation atomically
replaces the old supplement. This is never resume evidence.
"""

import json
from datetime import datetime, timezone

from sqlalchemy import text


FIELDS = ("communication", "teamwork", "stress_tolerance", "responsibility")
LEVELS = {"强", "中", "弱"}


def revision(profile: dict) -> int:
    report = profile.get("_user_self_report")
    if not isinstance(report, dict):
        return 0
    value = report.get("revision")
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def portrait_scoring_current(portrait: dict) -> bool:
    """Reject legacy D4=0 when no growth evidence supported that zero."""
    if not isinstance(portrait, dict):
        return False
    d4 = portrait.get("D4_potential")
    if not isinstance(d4, dict):
        return True
    score = d4.get("dimension_score")
    evidence = d4.get("evidence")
    return not (type(score) in (int, float) and score == 0
                and isinstance(evidence, list) and not evidence)


def portrait_current(profile: dict, portrait: dict) -> bool:
    return (isinstance(portrait, dict)
            and portrait.get("source_confirmation_task_id") ==
            profile.get("_resume_confirmation", {}).get("task_id")
            and portrait.get("source_self_report_revision", 0) == revision(profile)
            and portrait_scoring_current(portrait))


def validated_items(items: object) -> dict:
    if not isinstance(items, dict) or set(items) != set(FIELDS):
        raise ValueError("请为四项职业素养分别选择等级或暂不补充")
    validated = {}
    for key in FIELDS:
        item = items[key]
        if item is None:
            validated[key] = None
            continue
        if not isinstance(item, dict) or set(item) != {"level", "example"}:
            raise ValueError(f"{key} 的补充格式不正确")
        level, example = item["level"], item["example"]
        if not isinstance(level, str) or level not in LEVELS or not isinstance(example, str) or not 12 <= len(example.strip()) <= 500:
            raise ValueError(f"{key} 需要选择等级并写出 12–500 字的具体经历")
        validated[key] = {"level": level, "example": example.strip()}
    return validated


def save_self_report(mysql, *, student_id: str, task_id: str,
                     expected_revision: int, items: object) -> dict:
    if not isinstance(expected_revision, int) or isinstance(expected_revision, bool) or expected_revision < 0:
        raise ValueError("补充版本无效，请刷新后重试")
    validated = validated_items(items)
    if expected_revision == 0 and not any(item is not None for item in validated.values()):
        raise ValueError("请至少补充一项具体经历")
    # Lock order agrees with confirmation and L2 publication: owner profile first.
    from .pipeline import require_confirmed_profile
    from .pipeline_events import ConfirmationTransaction
    with mysql.engine.begin() as conn:
        conn.execute(text("SELECT student_id FROM student_profile WHERE student_id=:sid FOR UPDATE"),
                     {"sid": student_id}).first()
        profile = require_confirmed_profile(ConfirmationTransaction(conn), student_id)
        if profile["_resume_confirmation"]["task_id"] != task_id:
            raise ValueError("简历确认版本已变化，请刷新后重试")
        if revision(profile) != expected_revision:
            raise ValueError("补充内容已更新，请刷新后重试")
        report = {"source_confirmation_task_id": task_id,
                  "revision": expected_revision + 1,
                  "source_kind": "user_self_report",
                  "updated_at": datetime.now(timezone.utc).isoformat(),
                  "items": validated}
        profile["_user_self_report"] = report
        conn.execute(text("UPDATE student_profile SET profile_json=:profile, updated_at=NOW() "
                          "WHERE student_id=:sid"),
                     {"sid": student_id, "profile": json.dumps(profile, ensure_ascii=False)})
        from .pipeline_portrait_jobs import persist_portrait_intent_tx
        portrait_task_id = persist_portrait_intent_tx(
            conn, student_id=student_id, confirmation_task_id=task_id,
            self_report_revision=report["revision"])
    persisted = require_confirmed_profile(mysql, student_id)
    if persisted.get("_user_self_report") != report:
        raise RuntimeError("自述补充未通过权威记录回读验证")
    from .pipeline_portrait_jobs import enqueue_portrait_intent
    report["portrait_analysis"] = {"task_id": portrait_task_id,
                                   "delivery": enqueue_portrait_intent(mysql, portrait_task_id)}
    return report
