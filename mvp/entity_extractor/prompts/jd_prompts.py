"""
JD (Job Description) Prompt 模块

JD 专用子任务：job_info / requirements / description
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# System Prompt
# ---------------------------------------------------------------------------

JD_SYSTEM_PROMPT = (
    "你是一个精确的职位描述信息抽取助手。"
    "你的输入是职位描述文本。"
    "你只需要提取指定类型的信息，返回严格的JSON格式。"
    "如果某个字段在文本中找不到，设为null。"
)

# ---------------------------------------------------------------------------
# 子任务 Prompt
# ---------------------------------------------------------------------------

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
    builder = _JD_PROMPT_BUILDERS.get(task_type)
    if builder is None:
        raise ValueError(f"未知JD任务类型: {task_type}")
    return builder(jd_text, pre_annotations)


def _build_job_info_prompt(
    jd_text: str,
    pre_annotations: Optional[Dict] = None,
) -> List[Dict[str, str]]:
    """职位基本信息"""
    user_content = f"""请从以下职位描述中提取职位基本信息。

职位描述：
{jd_text}

请返回JSON格式：
{{
    "job_title": "职位名称",
    "company": "公司名称或null",
    "location": "工作地点或null",
    "salary_range": "薪资范围或null",
    "job_type": "全职/兼职/实习 或null"
}}"""

    return [
        {"role": "system", "content": JD_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def _build_requirements_prompt(
    jd_text: str,
    pre_annotations: Optional[Dict] = None,
) -> List[Dict[str, str]]:
    """任职要求"""
    constraints = ""
    if pre_annotations and pre_annotations.get("detected_skills"):
        skills_str = ", ".join(pre_annotations["detected_skills"][:15])
        constraints += f"\n已预匹配到的技能要求: [{skills_str}]"

    user_content = f"""请从以下职位描述中提取任职要求。
{constraints}

职位描述：
{jd_text}

请返回JSON格式：
{{
    "required_skills": ["必需技能1", "必需技能2"],
    "preferred_skills": ["加分技能1"],
    "min_education": "最低学历要求或null",
    "min_experience_years": 最低工作年限数字或null,
    "other_requirements": ["其他要求"]
}}"""

    return [
        {"role": "system", "content": JD_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def _build_description_prompt(
    jd_text: str,
    pre_annotations: Optional[Dict] = None,
) -> List[Dict[str, str]]:
    """职位描述/职责"""
    user_content = f"""请从以下职位描述中提取工作职责和岗位描述。

职位描述：
{jd_text}

请返回JSON格式：
{{
    "responsibilities": ["职责1", "职责2"],
    "team_info": "团队信息或null",
    "benefits": ["福利1", "福利2"]
}}"""

    return [
        {"role": "system", "content": JD_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


_JD_PROMPT_BUILDERS = {
    "job_info": _build_job_info_prompt,
    "requirements": _build_requirements_prompt,
    "description": _build_description_prompt,
}
