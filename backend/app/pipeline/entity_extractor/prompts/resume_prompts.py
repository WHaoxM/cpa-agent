"""
简历 Prompt 模块 — 针对 Qwen3-0.6B 的极简指令设计

核心设计原则（v5.1 重写）：
1. 0.6B 模型做"确认+结构化"，不做"发现"— Phase 2 已完成实体发现
2. 严禁中文占位符（"公司名称"等）— 防止模板复制幻觉
3. 强制 XML 标签提取 — <skill>/<role>/<date>/<degree> 内容必须出现在输出中
4. 极简扁平 JSON — 不要求嵌套对象，降低 schema 复杂度
5. pre_extracted 以"已确认实体"注入，模型只需填入 JSON 字段
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# System Prompt — 极短，强制 XML 标签规则
# ---------------------------------------------------------------------------

RESUME_SYSTEM_PROMPT = (
    "Extract resume info from XML-tagged text. "
    "Input: first line is 【title】, body has <skill>/<role>/<date>/<degree> tags. "
    "Rules: 1) Title with '公司/实验室/集团'→company, '系统/平台/项目'→project; "
    "2) All tagged words MUST appear in JSON output; "
    "3) NO invented content; 4) Empty fields use \"\". "
    "Output ONLY valid JSON, no explanation."
)

# ---------------------------------------------------------------------------
# 子任务 Prompt 模板
# ---------------------------------------------------------------------------

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
    builder = _PROMPT_BUILDERS.get(task_type)
    if builder is None:
        raise ValueError(f"未知任务类型: {task_type}")
    return builder(indexed_text, pre_annotations or {})


def _build_basic_info_prompt(
    indexed_text: str,
    pre: Dict,
) -> List[Dict[str, str]]:
    """基本信息抽取 — 极简"""
    user_content = f"""提取此人的基本信息。

文本：
{indexed_text}

输出JSON：
{{"name": "", "position": "", "phone": "", "email": ""}}"""

    return [
        {"role": "system", "content": RESUME_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def _build_education_prompt(
    indexed_text: str,
    pre: Dict,
) -> List[Dict[str, str]]:
    """教育经历抽取 — 注入已确认的学位和日期"""
    confirmed = ""
    degrees = pre.get("detected_degrees", [])
    dates = pre.get("detected_dates", [])
    if degrees:
        confirmed += f"\n已确认学位：{', '.join(degrees)}"
    if dates:
        confirmed += f"\n已确认日期：{', '.join(dates[:4])}"

    user_content = f"""提取教育经历。{confirmed}

文本：
{indexed_text}

输出JSON：
{{"school": "", "degree": "", "major": "", "start_date": "", "end_date": ""}}

示例：{{"school": "Tsinghua University", "degree": "Master", "major": "CS", "start_date": "2020/09", "end_date": "2023/06"}}"""

    return [
        {"role": "system", "content": RESUME_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def _build_experience_prompt(
    indexed_text: str,
    pre: Dict,
) -> List[Dict[str, str]]:
    """工作/项目经历抽取 — 注入已确认的角色、技能、日期"""
    confirmed = ""
    skills = pre.get("detected_skills", [])
    dates = pre.get("detected_dates", [])
    if skills:
        confirmed += f"\n已确认技能：{', '.join(skills[:10])}"
    if dates:
        confirmed += f"\n已确认日期：{', '.join(dates[:4])}"

    user_content = f"""提取工作或项目经历。文本中<role>标签是角色，<date>标签是时间，<skill>标签是技能，必须全部填入JSON。{confirmed}

标题分类规则：
- 标题含"公司/实验室/集团/科技/网络/有限"→ 填入 company
- 标题含"系统/平台/项目/课题/引擎/知识库"→ 填入 project
- 例如："省重点人工智能实验室" → project（不是company）

正例：
输入：【字节跳动】
<role>后端开发</role>
<date>2023/07-2024/06</date>
<skill>Python</skill>
输出：{{"company": "字节跳动", "project": "", "position": "后端开发", "start_date": "2023/07", "end_date": "2024/06", "skills": ["Python"]}}

反例（错误）：
输入：【省重点人工智能实验室】
<role>核心开发者</role>
<date>2024/11-至今</date>
错误输出：{{"company": "省重点人工智能实验室", ...}}  ← 实验室不是公司，应填入project
正确输出：{{"company": "", "project": "省重点人工智能实验室", "position": "核心开发者", "start_date": "2024/11", "end_date": "至今", "skills": []}}

文本：
{indexed_text}

输出JSON：
{{"company": "", "project": "", "position": "", "start_date": "", "end_date": "", "skills": []}}"""

    return [
        {"role": "system", "content": RESUME_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def _build_skills_prompt(
    indexed_text: str,
    pre: Dict,
) -> List[Dict[str, str]]:
    """技能抽取 — 注入 AC 算法已确认技能，模型只需确认+补充"""
    skills = pre.get("detected_skills", [])
    if skills:
        skills_str = ", ".join(skills[:20])
        constraint = f"\n以下技能已由系统确认，必须全部保留：[{skills_str}]。你可以补充文本中遗漏的技能。"
    else:
        constraint = "\n提取文本中所有<skill>标签内的技能词。"

    user_content = f"""提取所有技能。{constraint}

文本：
{indexed_text}

输出JSON：
{{"skills": []}}

示例：{{"skills": ["Python", "FastAPI", "Docker", "LangChain"]}}"""

    return [
        {"role": "system", "content": RESUME_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


# Prompt 构造器注册表
_PROMPT_BUILDERS = {
    "basic_info": _build_basic_info_prompt,
    "education": _build_education_prompt,
    "experience": _build_experience_prompt,
    "skills": _build_skills_prompt,
}
