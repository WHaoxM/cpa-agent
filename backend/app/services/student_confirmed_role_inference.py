"""Career direction exploration from the current confirmed resume only."""


def infer_confirmed_resume_roles(mysql, *, student_id: str, task_id: str,
                                 preferred_role: str | None = None) -> dict:
    from .career_role_inference import infer_roles_from_skills, normalize_skill_inputs
    from .pipeline import require_confirmed_profile

    profile = require_confirmed_profile(mysql, student_id)
    if profile["_resume_confirmation"]["task_id"] != task_id:
        raise ValueError("简历确认版本已变化，请刷新后重试")
    skills = profile.get("skills")
    if not isinstance(skills, list) or not normalize_skill_inputs(skills):
        raise ValueError("当前确认简历没有可用于方向探索的技术词")

    roles = infer_roles_from_skills(skills, top_k=5, preferred_role=preferred_role)
    current = require_confirmed_profile(mysql, student_id)
    if (current["_resume_confirmation"]["task_id"] != task_id
            or current.get("skills") != skills):
        raise ValueError("简历确认版本在方向推断期间发生变化，请重试")
    return {"source_confirmation_task_id": task_id,
            "source_kind": "confirmed_resume", "scoring_eligible": False,
            "exploration_only": True, "roles": roles}
