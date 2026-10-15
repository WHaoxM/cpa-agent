"""Owner-authored technical skills for career exploration only.

This snapshot is deliberately separate from confirmed resume skills and the
scored behavioral self-report. L2, matching and resume reports must not read it.
"""

import json
from datetime import datetime, timezone

from sqlalchemy import text


MAX_SKILLS = 12
MAX_NAME_LENGTH = 64


def revision(profile: dict) -> int:
    report = profile.get("_skill_exploration")
    if not isinstance(report, dict):
        return 0
    value = report.get("revision")
    return value if type(value) is int and value > 0 else 0


def current_report(profile: dict) -> dict | None:
    report = profile.get("_skill_exploration")
    confirmation = profile.get("_resume_confirmation") or {}
    if (not isinstance(report, dict) or not isinstance(confirmation, dict)
            or report.get("source_confirmation_task_id") != confirmation.get("task_id")
            or report.get("source_kind") != "user_self_report"
            or report.get("scoring_eligible") is not False
            or revision(profile) == 0
            or not isinstance(report.get("skills"), list)):
        return None
    try:
        if validated_skills(report["skills"]) != report["skills"]:
            return None
    except ValueError:
        return None
    return report


def validated_skills(value: object) -> list[str]:
    if not isinstance(value, list) or len(value) > MAX_SKILLS:
        raise ValueError(f"请提交不超过 {MAX_SKILLS} 项技能")
    skills, seen = [], set()
    for raw in value:
        if not isinstance(raw, str):
            raise ValueError("技能名称必须是文本")
        name = raw.strip()
        if (not name or len(name) > MAX_NAME_LENGTH
                or any(ord(char) < 32 or ord(char) == 127 for char in name)):
            raise ValueError(f"每项技能须为 1–{MAX_NAME_LENGTH} 字的单行名称")
        key = name.casefold()
        if key not in seen:
            seen.add(key)
            skills.append(name)
    return skills


def save_skill_exploration(mysql, *, student_id: str, task_id: str,
                           expected_revision: int, skills: object) -> dict:
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("补充版本无效，请刷新后重试")
    names = validated_skills(skills)
    if expected_revision == 0 and not names:
        raise ValueError("请至少补充一项技能")
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
                  "scoring_eligible": False,
                  "updated_at": datetime.now(timezone.utc).isoformat(),
                  "skills": names}
        profile["_skill_exploration"] = report
        conn.execute(text("UPDATE student_profile SET profile_json=:profile, updated_at=NOW() "
                          "WHERE student_id=:sid"),
                     {"sid": student_id, "profile": json.dumps(profile, ensure_ascii=False)})
    persisted = require_confirmed_profile(mysql, student_id)
    if current_report(persisted) != report:
        raise RuntimeError("技能自述未通过权威记录回读验证")
    return report


def infer_skill_exploration(mysql, *, student_id: str, task_id: str,
                            preferred_role: str | None = None) -> dict:
    from .pipeline import require_confirmed_profile
    from .career_role_inference import infer_roles_from_skills
    profile = require_confirmed_profile(mysql, student_id)
    if profile["_resume_confirmation"]["task_id"] != task_id:
        raise ValueError("简历确认版本已变化，请刷新后重试")
    report = current_report(profile)
    if not report or not report["skills"]:
        raise ValueError("请先保存用于方向探索的技能自述")
    roles = infer_roles_from_skills(report["skills"], top_k=5, preferred_role=preferred_role)
    current = current_report(require_confirmed_profile(mysql, student_id))
    if current != report:
        raise ValueError("技能自述在方向推断期间发生变化，请重试")
    return {"source_confirmation_task_id": task_id, "revision": report["revision"],
            "source_kind": "user_self_report", "scoring_eligible": False,
            "exploration_only": True, "roles": roles}
