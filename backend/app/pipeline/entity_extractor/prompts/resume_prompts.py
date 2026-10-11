"""
简历 Prompt 模块 — 针对 Qwen3-0.6B 的极简指令设计

所有 prompt 来自 config/prompts/registry.yaml 注册的实体:
  - entity.resume.system
  - entity.resume.user.basic_info
  - entity.resume.user.education
  - entity.resume.user.experience
  - entity.resume.user.skills

核心设计原则（v5.1 重写）：
1. 0.6B 模型做"确认+结构化"，不做"发现"— Phase 2 已完成实体发现
2. 严禁中文占位符（"公司名称"等）— 防止模板复制幻觉
3. 强制 XML 标签提取 — <skill>/<role>/<date>/<degree> 内容必须出现在输出中
4. 极简扁平 JSON — 不要求嵌套对象，降低 schema 复杂度
5. pre_extracted 以"已确认实体"注入，模型只需填入 JSON 字段
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from config.prompt_loader import load_prompt

# ---------------------------------------------------------------------------
# 子任务 ID → Prompt Registry 的 user prompt id 映射
# ---------------------------------------------------------------------------
_USER_PROMPT_IDS = {
    "basic_info": "entity.resume.user.basic_info",
    "education": "entity.resume.user.education",
    "experience": "entity.resume.user.experience",
    "skills": "entity.resume.user.skills",
}


def get_resume_prompt(
    task_type: str,
    indexed_text: str,
    pre_annotations: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, str]]:
    """
    生成简历抽取的 chat messages。

    Args:
        task_type: "basic_info" | "education" | "experience" | "skills"
        indexed_text: 带 XML 标签的文本（来自 Phase 2 tagged_text）
        pre_annotations: Phase 2 的预标注结果（已确认的技能、日期等）

    Returns:
        Qwen3 chat_template 格式的 messages 列表
    """
    if task_type not in _USER_PROMPT_IDS:
        raise ValueError(f"未知任务类型: {task_type}")

    pre = pre_annotations or {}
    system_prompt = load_prompt("entity.resume.system")
    user_prompt = _build_user_prompt(task_type, indexed_text, pre)

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def _build_user_prompt(task_type: str, indexed_text: str, pre: Dict) -> str:
    """根据任务类型组装 user content（从配置加载搜索辅助指令）"""
    # 从配置加载搜索辅助指令
    from config.loader import get_extraction_config, get_grep_config

    config = get_extraction_config()
    search_hints = config.get('search_hints', {})
    keywords_config = get_grep_config().get('resume_keywords', {})

    # 构建搜索辅助指令
    search_instruction = ""
    if task_type in search_hints and task_type in keywords_config:
        keywords = keywords_config[task_type]
        search_instruction = search_hints[task_type].format(
            matched_segments=f"[关键词：{', '.join(keywords[:5])}]"
        )

    # 根据任务类型组装用户提示
    user_id = _USER_PROMPT_IDS[task_type]

    if task_type == "basic_info":
        return load_prompt(user_id, indexed_text=indexed_text)

    if task_type == "education":
        confirmed = _format_confirmed_education(pre)
        # 添加搜索辅助指令到confirmed
        if search_instruction:
            confirmed = f"{confirmed}\n\n{search_instruction}"
        return load_prompt(user_id, indexed_text=indexed_text, confirmed=confirmed)

    if task_type == "experience":
        confirmed = _format_confirmed_experience(pre)
        # 添加搜索辅助指令到confirmed
        if search_instruction:
            confirmed = f"{confirmed}\n\n{search_instruction}"
        return load_prompt(user_id, indexed_text=indexed_text, confirmed=confirmed)

    if task_type == "skills":
        constraint = _format_skills_constraint(pre)
        # 添加搜索辅助指令到constraint
        if search_instruction:
            constraint = f"{constraint}\n\n{search_instruction}"
        return load_prompt(user_id, indexed_text=indexed_text, constraint=constraint)

    raise ValueError(f"未实现的任务类型: {task_type}")


def _format_confirmed_education(pre: Dict) -> str:
    """教育 prompt: 已确认学位/日期"""
    parts = []
    degrees = pre.get("detected_degrees", [])
    if degrees:
        parts.append(f"\n已确认学位：{', '.join(degrees)}")
    dates = pre.get("detected_dates", [])
    if dates:
        parts.append(f"\n已确认日期：{', '.join(dates[:4])}")
    return "".join(parts)


def _format_confirmed_experience(pre: Dict) -> str:
    """工作/项目 prompt: 已确认技能/日期"""
    parts = []
    skills = pre.get("detected_skills", [])
    if skills:
        parts.append(f"\n已确认技能：{', '.join(skills[:10])}")
    dates = pre.get("detected_dates", [])
    if dates:
        parts.append(f"\n已确认日期：{', '.join(dates[:4])}")
    return "".join(parts)


def _format_skills_constraint(pre: Dict) -> str:
    """技能 prompt: 强制保留已确认技能"""
    skills = pre.get("detected_skills", [])
    if skills:
        skills_str = ", ".join(skills[:20])
        return f"\n以下技能已由系统确认，必须全部保留：[{skills_str}]。你可以补充文本中遗漏的技能。"
    return "\n提取文本中所有<skill>标签内的技能词。"
