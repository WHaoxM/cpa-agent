"""
Thinking trace for UI — Claude/Qoder style summarized deliberation.

Channel split (see thinking_egress):
- Operator playbook stays in prompts / logs.
- UI thinking is first-person deliberation about THIS turn's facts.
Never pass playbook strings into thinking_steps.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from .thinking_egress import sanitize_thinking_steps

_THINKING_RE = re.compile(
    r"<<<THINKING>>>\s*(.*?)\s*<<<ANSWER>>>\s*(.*)\s*\Z",
    re.DOTALL | re.IGNORECASE,
)

_ASK_THINKING_FALLBACK = """
请严格按以下两段输出（都用中文）：

<<<THINKING>>>
用第一人称内部独白写「本轮我在想什么」（给产品思考区看，不是给你自己的操作清单）。
必须像在推演用户问题，而不是复述系统规则。覆盖：
1) 用户真正在问什么，有无隐含诉求
2) 我这轮掌握了哪些材料/缺什么，能说到哪一步
3) 我在几种答法之间如何取舍
4) 正式回复会优先交代什么、收在哪个具体下一步
硬性禁止出现在 THINKING 中：
- 任何内部字段名或工具英文名
- 「黑板」「CPA架构」「底层契约」、student_id 等实现黑话
- 把系统注入的状态卡或本指令原文复述进思考区
- 操作口吻：「先确认…再决定…」「应…」「正式回复应…」「收束到…」
要求：至少 5 段，每段 1～3 句；用「我」；允许自我质疑；不要写最终成稿；不要 emoji。
<<<ANSWER>>>
给用户的正式回复（Markdown，无 emoji）。语气专业、可行动；不要暴露内部实现细节。
""".strip()


def get_ask_thinking_instruction() -> str:
    """Load Ask thinking operator instruction through PromptSelector."""
    try:
        from .modes import InteractionMode
        from .prompt_selector import PromptSelectionKey, get_prompt_selector

        return get_prompt_selector().select(
            PromptSelectionKey(
                engine="supervisor",
                interaction_mode=InteractionMode.ASK,
                content_kind="thinking",
            )
        ).system
    except Exception:
        return _ASK_THINKING_FALLBACK

_TOOL_ZH = {
    "attachment_read": "阅读附件",
    "db_query": "查询档案",
    "semantic_search": "语义检索",
    "graph_query": "知识图谱查询",
    "pipeline_run": "启动分析任务",
    "portrait_generate": "生成画像",
    "skill_match": "技能匹配",
    "report_generate": "生成报告",
}


def split_paragraphs(text: str) -> List[str]:
    chunks = re.split(r"\n\s*\n+", (text or "").strip())
    out: List[str] = []
    for chunk in chunks:
        line = re.sub(r"\s+", " ", chunk).strip()
        if line:
            out.append(line)
    return out


def parse_thinking_answer(raw: str) -> Tuple[List[str], Optional[str]]:
    """Parse <<<THINKING>>>/<<<ANSWER>>> block. Returns (steps, answer_or_None)."""
    text = (raw or "").strip()
    if not text:
        return [], None
    m = _THINKING_RE.search(text)
    if not m:
        return [], None
    steps = sanitize_thinking_steps(split_paragraphs(m.group(1)))
    answer = (m.group(2) or "").strip()
    return steps, answer or None


def ensure_min_depth(steps: List[str], *, fallback: List[str], min_n: int = 4) -> List[str]:
    merged = [s for s in steps if s and s.strip()]
    if len(merged) >= min_n:
        return sanitize_thinking_steps(merged[:12])
    for line in fallback:
        if line and line not in merged:
            merged.append(line)
        if len(merged) >= min_n:
            break
    return sanitize_thinking_steps(merged[:12])


def synthesize_ask_ui_thinking(
    message: str,
    *,
    path: str,
    filenames: Optional[List[str]] = None,
    mount_ok: bool = True,
    upgrade_to: Optional[str] = None,
) -> List[str]:
    """
    Fact-grounded UI thinking for Ask paths that skip/fail the model scratchpad.
    First-person deliberation only — never operator playbook.
    """
    text = (message or "").strip()
    preview = text if len(text) <= 80 else text[:77] + "…"
    names = [n for n in (filenames or []) if n]
    file_clause = "、".join(f"《{n}》" for n in names[:3]) if names else ""

    if path == "ask_attachments" or path.startswith("ask_attachments"):
        steps = [
            (
                f"用户附上了{file_clause}。"
                if file_clause
                else "用户附上了材料。"
            )
            + (
                "我先确认本轮已成功挂载，并核对文档访问层给出的完整性标记（complete/has_more）。"
                if mount_ok
                else "挂载似乎有告警，我需要先说明材料可能不完整，再谨慎作答。"
            ),
            "若标记为全文完整，我应按全文（含教育、项目、奖项尾部）作答，不能假装只看到开头关键词。",
            f"用户原话是「{preview or '请基于附件给建议'}」。我区分：简历已写明的事实 vs 简历未写的意向（城市/岗位偏好仍需问）。",
            "完整的个人画像、人岗匹配和正式报告仍需要切换到执行模式；本轮基于附件事实给建议与澄清。",
            "若文档未读完（has_more）或关键事实不足，我会明确说清缺口，而不是用「不太清楚」掩盖未读完。",
        ]
    elif path == "ask_greeting":
        steps = [
            "这是短寒暄。我先建立可继续对话的入口，而不是立刻展开长篇能力说明书。",
            "我可以解释路径与给建议；若对方要一键出画像/匹配/报告，需要切换到执行模式。",
            "只回一句你好信息太少；我会带一句我能帮什么，并给一个低成本下一步。",
            "语气保持专业、短、可继续，避免堆功能清单。",
            "收尾时我会问一个具体问题（意向岗位或是否有简历），比并列十个功能更有效。",
        ]
    elif path == "ask_capability":
        steps = [
            "用户在问我能做什么，不是在继续上一轮的岗位分析。",
            "本轮我没有假设已知其画像或目标岗位；新对话应从零对齐。",
            "我会说明可解释/可建议的范围，并诚实交代执行类动作要切换模式。",
            "结构上我会按「能帮什么 → 模式边界 → 一个可继续的问题」来说。",
            "等对方给出目标或简历后，再进入具体建议。",
        ]
    elif path == "ask_upgrade_hint":
        mode = upgrade_to or "Agent"
        steps = [
            f"用户原话「{preview}」带有较强的执行意图。",
            "在问答模式下我不应半执行管线，否则容易给出看起来完整、其实无依据的结论。",
            f"我会解释思路，并明确建议切换到 {mode} 后再发同样的请求。",
            "同时保留一个低成本下一步：先补简历或说清目标岗位。",
            "正式回复要短、可行动，不假装已经跑完分析。",
        ]
    else:
        steps = [
            f"用户原话是「{preview}」。我先判断：是在探能力边界、要职业建议，还是已经要我直接执行分析。",
            "当前是问答模式：我可以解释路径与框架，但不能假装已经生成了个人画像、匹配结果或完整报告。",
            "空泛鼓励没有用；我会给决策框架，并点出还缺哪些个人材料。",
            "正式回复会先说明「需要个人材料才能认真分析」，再给简框架，最后落到上传简历或切换执行模式。",
            "若对方其实要一键出报告/匹配，我会明确建议切换模式，而不是在问答模式里半执行。",
        ]
    return sanitize_thinking_steps(steps)


def ask_fallback_trace(
    message: str,
    *,
    path: str,
    upgrade_to: Optional[str] = None,
    filenames: Optional[List[str]] = None,
    mount_ok: bool = True,
) -> List[str]:
    """Backward-compatible name → UI thinking synthesizer (not operator playbook)."""
    return synthesize_ask_ui_thinking(
        message,
        path=path,
        filenames=filenames,
        mount_ok=mount_ok,
        upgrade_to=upgrade_to,
    )


def synthesize_agent_trace(
    *,
    message: str,
    strategy: str,
    active_subagent: str,
    tool_calls: Optional[List[Any]] = None,
    supervisor_view: Optional[Dict[str, Any]] = None,
    plan_results: Optional[List[Dict[str, Any]]] = None,
    permission_denials: Optional[List[Any]] = None,
) -> List[str]:
    text = (message or "").strip()
    preview = text if len(text) <= 90 else text[:87] + "…"
    view = supervisor_view or {}
    tools = tool_calls or []

    role_zh = {
        "ask": "问答助手",
        "qa": "问答助手",
        "qa_subagent": "问答助手",
        "profile": "画像专家",
        "profile_subagent": "画像专家",
        "match": "匹配专家",
        "match_subagent": "匹配专家",
        "report": "报告专家",
        "report_subagent": "报告专家",
        "planner": "规划专家",
        "planner_subagent": "规划专家",
        "supervisor": "编排助手",
        "lead": "多专家协调",
    }.get(active_subagent, "助手")

    strategy_zh = {
        "ask": "轻量问答",
        "ask_with_attachments": "基于附件的问答",
        "agent": "单专家执行",
        "agent_clarify": "澄清后再执行",
        "gate_block": "前置条件未满足",
        "experts": "多专家协作",
        "ask_upgrade_hint": "建议升级模式",
    }.get(strategy, "本轮处理")

    steps: List[str] = [
        f"用户诉求是「{preview}」。我先判断这是问答、单步任务，还是需要多步协作。",
        f"本轮我采用「{strategy_zh}」，由{role_zh}承接。"
        + (
            f"路由把握大约 {view.get('confidence')}。"
            if view.get("confidence") is not None
            else ""
        ),
    ]

    # Attachment facts if present
    file_ids = view.get("file_ids") or []
    filenames = view.get("filenames") or []
    if strategy == "ask_with_attachments" or file_ids or filenames:
        return ensure_min_depth(
            synthesize_ask_ui_thinking(
                message,
                path="ask_attachments",
                filenames=list(filenames) if filenames else None,
                mount_ok=not bool(view.get("mount_errors")),
            ),
            fallback=steps,
            min_n=4,
        )

    if strategy == "agent_clarify":
        steps.extend(
            [
                "我掌握的信息还不足以安全选择画像、匹配、报告或规划路径，猜错会浪费一轮。",
                "所以我先澄清目标，只问一个最关键约束。",
                "等用户确认后，再进入对应专家并决定是否调用工具。",
            ]
        )
    elif strategy == "gate_block":
        steps.extend(
            [
                "还缺少上游结果（例如个人画像或匹配），强行做下游只会得到空结论。",
                "我会向用户解释缺口与补齐顺序，而不是返回技术失败细节。",
                "可以建议切换 Experts 走端到端，或先完成画像步骤。",
            ]
        )
    elif strategy == "experts":
        template = view.get("plan_template") or "多步计划"
        steps.append(f"多专家协作按「{template}」推进；我会以是否写出有效业务结果为准，而不只看调用是否报错。")
        for i, row in enumerate(plan_results or [], 1):
            if not isinstance(row, dict):
                continue
            ok = "完成（已有有效结果）" if row.get("ok") else "未达成"
            sub = {
                "profile": "画像", "match": "匹配", "report": "报告",
                "planner": "规划", "qa": "问答",
            }.get(str(row.get("subagent") or ""), "步骤")
            err = f"（{row.get('error')}）" if row.get("error") else ""
            steps.append(f"步骤 {i}（{sub}）→ {ok}{err}。")
        steps.append("最后我会区分已成功步骤与未达成步骤，不把空结果说成已完成。")
    elif strategy == "experts_clarify":
        goal = str(view.get("goal_summary") or "").strip()
        job = str(view.get("user_job") or "").strip()
        missing = view.get("missing_slots") or []
        sketch = view.get("plan_sketch") or []
        if goal:
            steps.append(f"我理解用户意图是：{goal}")
        else:
            steps.append("我先重述用户意图，再决定是否启动多专家计划。")
        if job == "meta":
            steps.append("这是模式/能力类问题，我只解释 Experts，不跑流水线。")
        elif missing:
            labels = "、".join(str(m) for m in missing[:4])
            steps.append(f"执行条件未齐，还缺：{labels}；我先澄清再行动。")
        else:
            steps.append("目标或部分前置仍不清，我先澄清，避免空跑画像/匹配/报告。")
        if sketch:
            steps.append("若确认执行，草案是：" + " → ".join(str(s) for s in sketch[:4]))
        steps.append("我会给用户可执行的下一步选项，而不是假装已完成计划。")
    else:
        allow_tools = view.get("allow_tools")
        if allow_tools is False:
            steps.append("本轮我不调用业务工具：现有上下文已足够回答，避免空转。")
        elif tools:
            steps.append("需要外部证据时我再调用工具；每个工具对应一个可验证问题。")
            for tc in tools[:5]:
                if not isinstance(tc, dict):
                    continue
                name = str(tc.get("tool_name") or tc.get("name") or tc.get("tool") or "工具")
                name_zh = _TOOL_ZH.get(name, "工具调用")
                summary = str(tc.get("summary") or tc.get("result_preview") or "").strip()
                summary = summary[:120] + ("…" if len(summary) > 120 else "")
                steps.append(f"{name_zh}：{summary or '已返回结果，纳入依据。'}")
            steps.append("汇总依据后，我按结论→依据→下一步组织回复，不粘贴原始返回。")
        else:
            steps.extend(
                [
                    "本轮未调用工具：会话上下文已足够形成可用回答。",
                    "我会按「先结论，再关键依据，最后给一个可执行下一步」来组织。",
                    "若后续需要画像/匹配/报告，再明确建议切换执行模式。",
                ]
            )

    denials = permission_denials or []
    if denials:
        first = denials[0] if isinstance(denials[0], dict) else {}
        reason = str(first.get("reason") or "部分能力暂不可用")[:140]
        from .thinking_egress import sanitize_thinking_line
        reason = sanitize_thinking_line(reason)
        steps.append(f"本轮有能力边界限制：{reason}。回复里需诚实说明，不能伪造已执行成功。")

    return ensure_min_depth(
        steps,
        fallback=synthesize_ask_ui_thinking(message, path="ask_llm"),
        min_n=4,
    )
