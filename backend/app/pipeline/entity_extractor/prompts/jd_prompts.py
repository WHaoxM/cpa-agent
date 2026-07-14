"""
JD (Job Description) Prompt 模块

所有 prompt 来自 config/prompts/registry.yaml 注册的实体:
  - entity.jd.system
  - entity.jd.user.job_info
  - entity.jd.user.requirements
  - entity.jd.user.description

JD 专用子任务：job_info / requirements / description
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from config.prompt_loader import load_prompt

# ---------------------------------------------------------------------------
# 子任务 ID → Prompt Registry 的 user prompt id 映射
# ---------------------------------------------------------------------------
_USER_PROMPT_IDS = {
    "job_info": "entity.jd.user.job_info",
    "requirements": "entity.jd.user.requirements",
    "description": "entity.jd.user.description",
}


def get_jd_prompt(
    task_type: str,
    jd_text: str,
    pre_annotations: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, str]]:
    """
    生成 JD 抽取的 chat messages。

    Args:
        task_type: "job_info" | "requirements" | "description"
        jd_text: JD 文本
        pre_annotations: Phase 2 的预标注结果

    Returns:
        Qwen3 chat_template 格式的 messages 列表
    """
    if task_type not in _USER_PROMPT_IDS:
        raise ValueError(f"未知JD任务类型: {task_type}")

    pre = pre_annotations or {}
    system_prompt = load_prompt("entity.jd.system")
    user_prompt = _build_user_prompt(task_type, jd_text, pre)

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def _build_user_prompt(task_type: str, jd_text: str, pre: Dict) -> str:
    """根据任务类型组装 user content（base + tone 通过 load_prompt 完成）"""
    user_id = _USER_PROMPT_IDS[task_type]
    if task_type == "job_info":
        return load_prompt(user_id, jd_text=jd_text)

    if task_type == "requirements":
        constraints = _format_requirements_constraints(pre)
        return load_prompt(user_id, jd_text=jd_text, constraints=constraints)

    if task_type == "description":
        return load_prompt(user_id, jd_text=jd_text)

    raise ValueError(f"未实现的JD任务类型: {task_type}")


def _format_requirements_constraints(pre: Dict) -> str:
    """任职要求 prompt: 已预匹配到的技能"""
    skills = pre.get("detected_skills", [])
    if skills:
        skills_str = ", ".join(skills[:15])
        return f"\n已预匹配到的技能要求: [{skills_str}]"
    return ""
