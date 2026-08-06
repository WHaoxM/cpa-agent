"""
ModeRuntime — first-class Ask / Agent / Experts dispatcher.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, List, Optional

from ...utils.logger import get_logger
from .blackboard import Blackboard, load_blackboard_from_session_context
from .budget import (
    MAX_HISTORY_MESSAGES,
)
from .context_assembler import AssembledContext, DefaultContextAssembler, TurnInputs
from .fast_path import (
    greeting_reply,
    is_capability_probe,
    is_pure_greeting,
    keyword_route,
    qa_needs_tools,
    should_use_llm_router,
    visible_user_text,
    wants_document_content,
)
from .fork_jobs import enqueue as fork_enqueue
from .memory_store import get_memory_store
from .modes import (
    InteractionMode,
    budget_for,
    detect_upgrade_hint,
    parse_interaction_mode,
)
from .experts_lead import experts_lead_decide
from .plan import (
    build_plan_packets,
    commit_step_updates,
    run_plan,
)
from .prefix import tool_schema_digest
from .scoped_invoke import scoped_invoke
from .subagent_graphs import BUSINESS_SUBAGENTS, invoke_subagent_graph
from .task_packet import build_standard_packet
from .thinking_trace import (
    ask_fallback_trace,
    ensure_min_depth,
    get_ask_thinking_instruction,
    parse_thinking_answer,
    synthesize_agent_trace,
)
from .reply_egress import sanitize_user_reply
from .tool_egress import sanitize_tool_calls
from .tool_policy import tools_for
from .prompt_selector import PromptSelectionKey, get_prompt_selector

logger = get_logger("cpa-agent.agent_runtime.mode")


def _invoke_subagent(
    packet,
    blackboard,
    session_id,
    *,
    allow_tools: bool = True,
    interaction_mode: InteractionMode = InteractionMode.AGENT,
    student_id: Optional[str] = None,
    plan_tool_allowlist: Optional[set] = None,
):
    kwargs = dict(
        allow_tools=allow_tools,
        interaction_mode=interaction_mode,
        session_id=session_id,
        student_id=student_id,
        plan_tool_allowlist=plan_tool_allowlist,
    )
    if not allow_tools or interaction_mode == InteractionMode.ASK:
        return scoped_invoke(packet, blackboard, **kwargs)
    if packet.subagent in BUSINESS_SUBAGENTS:
        try:
            return invoke_subagent_graph(
                packet,
                blackboard,
                session_id=session_id,
                interaction_mode=interaction_mode,
                student_id=student_id,
                plan_tool_allowlist=plan_tool_allowlist,
            )
        except Exception as e:
            logger.warning("graph invoke failed, scoped fallback: %s", e)
            return scoped_invoke(packet, blackboard, **kwargs)
    return scoped_invoke(packet, blackboard, **kwargs)


def _llm_route(
    message: str,
    router_context: str,
) -> Optional[Dict[str, Any]]:
    import json
    import re

    try:
        from ...utils.llm_client import get_llm_client

        llm = get_llm_client()
        prompt = get_prompt_selector().select(
            PromptSelectionKey(
                engine="supervisor",
                interaction_mode=InteractionMode.AGENT,
                content_kind="router",
            ),
            variables={
                "router_context": router_context,
                "message": message,
            },
        )
        raw = llm.chat(
            messages=[{"role": "user", "content": prompt.system}],
            temperature=0.1,
            max_tokens=400,
        )
        if not raw:
            return None
        raw = raw.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception as e:
        logger.info("llm_route skip: %s", e)
        return None


class ModeRuntime:
    def __init__(self, assembler: Optional[DefaultContextAssembler] = None):
        self._assembler = assembler or DefaultContextAssembler()

    def chat(
        self,
        message: str,
        *,
        session_id: Optional[str] = None,
        student_id: Optional[str] = None,
        history: Optional[List[Dict[str, str]]] = None,
        conversation_summary: str = "",
        session_context: Optional[Dict[str, Any]] = None,
        plan_mode: bool = False,
        confirmed_constraints: Optional[str] = None,
        interaction_mode: Optional[str] = None,
        attachment_ids: Optional[List[str]] = None,
        content_parts: Optional[List[Dict[str, Any]]] = None,
        voice_turn_id: Optional[str] = None,
        use_memory: Optional[bool] = None,
        prepared_turn: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        start = time.time()
        ctx = session_context if isinstance(session_context, dict) else {}
        session_mode = ctx.get("interaction_mode")
        mode = parse_interaction_mode(
            interaction_mode, plan_mode=plan_mode, session_mode=session_mode,
        )
        budget = budget_for(mode)

        # Media: API may have already mounted/normalized this turn for STM.
        if prepared_turn is not None:
            prepared = prepared_turn
        else:
            from .media.prepare_turn import prepare_user_turn

            prepared = prepare_user_turn(
                message or "",
                session_id=session_id,
                student_id=student_id,
                attachment_ids=attachment_ids,
                content_parts=content_parts,
                voice_turn_id=voice_turn_id,
                mount=True,
            )
        parts = prepared["parts"]
        turn_file_ids = prepared["turn_file_ids"]
        mount_errors = prepared["mount_errors"]
        attach_card = prepared["attach_card"] or ""
        # Ask card: allow attachment_read instruction only when we will bind that tool
        if mode == InteractionMode.ASK and turn_file_ids:
            from .media.context_mount import build_attachment_mount_card
            attach_card = build_attachment_mount_card(
                session_id, parts, turn_file_ids=turn_file_ids, allow_attachment_read=True,
            )
        message = prepared["visible_text"] or (message or "")
        message = visible_user_text(message)

        blackboard = load_blackboard_from_session_context(ctx)
        if student_id and not blackboard.has("student.profile_ref"):
            blackboard.write(
                "student.profile_ref",
                {"student_id": student_id, "summary": student_id},
                "supervisor",
            )
        if turn_file_ids:
            from .media.file_store import get_agent_file_service
            fs = get_agent_file_service()
            refs = []
            for fid in turn_file_ids:
                meta = fs.get(fid) or {}
                refs.append({
                    "file_id": fid,
                    "filename": meta.get("filename"),
                    "status": meta.get("status"),
                    "excerpt": (meta.get("excerpt") or "")[:120],
                })
            blackboard.write(
                "attachments.mounted",
                {
                    "summary": f"{len(refs)} this-turn",
                    "files": refs,
                    "mount_errors": mount_errors,
                },
                "supervisor",
            )

        if student_id and confirmed_constraints and budget.allow_memory_write_sync:
            try:
                get_memory_store().write_user_constraints(student_id, confirmed_constraints)
                blackboard.write(
                    "constraints.user",
                    {"summary": confirmed_constraints[:200], "text": confirmed_constraints},
                    "supervisor",
                )
            except Exception as e:
                logger.warning("USER.md write failed: %s", e)

        hist = list(history or [])[-MAX_HISTORY_MESSAGES:]
        fork_jobs: List[str] = []

        # LTM: Ask opt-in only; Agent/Experts mount a short digest for router (never subagents).
        mount_ltm = (mode == InteractionMode.ASK and use_memory is True) or (
            mode != InteractionMode.ASK and bool(budget.allow_memory_read)
        )
        memory_mount = ""
        if student_id and mount_ltm:
            try:
                memory_mount = get_memory_store().mount_for_supervisor(student_id)
            except Exception as e:
                logger.warning("memory mount failed: %s", e)

        digest = tool_schema_digest("qa")
        if mode == InteractionMode.AGENT:
            # stable multi-subagent digest for session prefix
            digest = " | ".join(
                f"{s}:{tool_schema_digest(s)}"
                for s in ("profile", "match", "report", "planner", "qa")
            )

        assembled = self._assembler.assemble(
            TurnInputs(
                mode=mode,
                message=message,
                blackboard=blackboard,
                history=hist,
                conversation_summary=conversation_summary,
                memory_digest=memory_mount,
                attachment_context=attach_card,
                agent_tool_digest=digest,
            )
        )

        upgrade_hint = detect_upgrade_hint(message, mode)

        if mode == InteractionMode.ASK:
            result = self._run_ask(
                message, blackboard, assembled.system_prompt, hist, start, session_id, upgrade_hint,
                assembled.memory_digest,
                student_id=student_id,
                turn_file_ids=turn_file_ids,
                mount_errors=mount_errors,
                assembled_context=assembled,
            )
        elif mode == InteractionMode.EXPERTS:
            result = self._run_experts(
                message, blackboard, assembled.router_context, "", start, session_id, plan_mode,
                assembled.memory_digest, student_id,
            )
        else:
            result = self._run_agent(
                message, blackboard, assembled.router_context, "", start, session_id,
                assembled.memory_digest,
                budget, student_id,
                turn_file_ids=turn_file_ids,
                mount_errors=mount_errors,
            )

        # Persist mode on orchestration
        blackboard.write(
            "orchestration.meta",
            {
                **(blackboard.get("orchestration.meta") or {}
                   if isinstance(blackboard.get("orchestration.meta"), dict) else {}),
                "interaction_mode": mode.value,
                "active_subagent": result.get("active_subagent"),
                "strategy": result.get("orchestration", {}).get("strategy"),
            },
            "supervisor",
        )
        result["interaction_mode"] = mode.value
        result["blackboard"] = blackboard.to_dict()
        result.setdefault("orchestration", {})
        result["orchestration"]["interaction_mode"] = mode.value
        if upgrade_hint:
            result["upgrade_hint"] = upgrade_hint
            result["orchestration"]["upgrade_hint"] = upgrade_hint

        # Fork: rollup never on request critical path
        if session_id and mode != InteractionMode.ASK:
            kind = fork_enqueue(session_id, "rollup_summary", {"keep_messages": 16})
            fork_jobs.append(kind)
        elif session_id and mode == InteractionMode.ASK and len(hist) >= 16:
            kind = fork_enqueue(session_id, "rollup_summary", {"keep_messages": 16})
            fork_jobs.append(kind)

        result["fork_jobs_enqueued"] = fork_jobs
        result["orchestration"]["fork_jobs_enqueued"] = fork_jobs
        result["engine"] = "supervisor"
        # ui_view egress: never ship model_view jargon / PII to the client
        if result.get("reply"):
            strat = (result.get("orchestration") or {}).get("strategy") or ""
            # Full-document dump needs a higher cap than coaching replies
            reply_cap = 48000 if strat == "document_content_dump" else 8000
            result["reply"] = sanitize_user_reply(str(result["reply"]), max_len=reply_cap)
        return result

    def _pack(
        self,
        *,
        reply: str,
        session_id: Optional[str],
        start: float,
        active_subagent: str,
        strategy: str,
        blackboard: Blackboard,
        tool_calls: Optional[List] = None,
        plan_results: Optional[List] = None,
        memory_mounted: bool = False,
        upgrade_hint: Optional[Dict] = None,
        supervisor_view: Optional[Dict] = None,
        permission_denials: Optional[List] = None,
        thinking_steps: Optional[List[str]] = None,
        message: str = "",
    ) -> Dict[str, Any]:
        denials = permission_denials or []
        from .thinking_egress import sanitize_thinking_steps

        steps = [str(s).strip() for s in (thinking_steps or []) if str(s).strip()]
        if not steps:
            steps = synthesize_agent_trace(
                message=message,
                strategy=strategy,
                active_subagent=active_subagent,
                tool_calls=tool_calls,
                supervisor_view=supervisor_view,
                plan_results=plan_results,
                permission_denials=denials,
            )
        # Claude/Qoder: thinking + reply UI never leak model_view
        steps = sanitize_thinking_steps(steps)
        reply_cap = 48000 if strategy == "document_content_dump" else 8000
        return {
            "reply": sanitize_user_reply(reply or "", max_len=reply_cap),
            # ui_view then final egress insurance
            "tool_calls": sanitize_tool_calls(tool_calls),
            "thinking_steps": steps,
            "session_id": session_id,
            "elapsed_seconds": round(time.time() - start, 2),
            "active_subagent": active_subagent,
            "engine": "supervisor",
            "permission_denials": denials,
            "orchestration": {
                "active_subagent": active_subagent,
                "strategy": strategy,
                "blackboard_refs": sorted(blackboard.to_dict().keys()),
                "memory_mounted": memory_mounted,
                "plan_results": plan_results,
                "upgrade_hint": upgrade_hint,
                "supervisor_view": supervisor_view,
                "permission_denials": denials,
                "thinking_steps": steps,
            },
            "blackboard": blackboard.to_dict(),
        }

    def _run_ask(
        self,
        message: str,
        blackboard: Blackboard,
        prefix: str,
        hist: List,
        start: float,
        session_id: Optional[str],
        upgrade_hint: Optional[Dict],
        memory_mount: str,
        student_id: Optional[str] = None,
        turn_file_ids: Optional[List[str]] = None,
        mount_errors: Optional[List[str]] = None,
        assembled_context: Optional[AssembledContext] = None,
    ) -> Dict[str, Any]:
        # Mounted files: Document Access Layer (complete pack or windowed), never silent 3k head-slice.
        if turn_file_ids and not (mount_errors or []):
            reply = ""
            thinking_steps: List[str] = []
            tool_calls: List[Dict[str, Any]] = []
            denials: List[Dict[str, str]] = []
            path = "ask_attachments_docs"
            pack_report: Dict[str, Any] = {}
            try:
                from ...utils.llm_client import get_llm_client
                from .media.document_access import materialize_for_ask

                # Server-side materialize (upgrade thin PDF + complete pack).
                # Do not rely on the model to page or ask users to re-upload.
                doc_pack, pack_report = materialize_for_ask(
                    list(turn_file_ids[:4]),
                    query=message or "",
                    student_id=student_id,
                    session_id=session_id,
                )
                logger.info(
                    "ask materialize files=%s all_complete=%s pack_chars=%s",
                    len(turn_file_ids[:4]),
                    pack_report.get("all_complete"),
                    len(doc_pack or ""),
                )
                if doc_pack.strip() and wants_document_content(message) and pack_report.get("all_complete"):
                    # User asked for body — deliver materialized text; never stall on「开始读简历」
                    path = "ask_document_dump"
                    reply = (
                        "已读取本轮挂载简历的完整正文（电话/邮箱已脱敏）。\n\n"
                        + doc_pack
                    )
                    thinking_steps = [
                        "用户要的是附件完整内容，不是求职咨询菜单。",
                        "文档访问层已标记 all_complete=true，正文已在服务端物化。",
                        "本轮直接返回脱敏全文，不再要求用户说「开始读简历」。",
                        "若还需优劣势梳理或岗位对照，可在下一轮说明目标方向。",
                    ]
                elif doc_pack.strip():
                    completeness = (
                        "【附件完整性】以下材料均为全文（complete=true），必须按全文作答"
                        "（含文末教育经历/奖项等）；禁止声称「只读到开头/专业技能」。"
                        "挂载卡上的 excerpt 不是正文。简历未写明的意向城市/岗位偏好仍需向用户确认。"
                        if pack_report.get("all_complete")
                        else (
                            "【附件完整性】部分材料超过单次窗口；正文标有 has_more 时，"
                            "你不得把首页当成全文。优先 attachment_read mode=find/section；"
                            "禁止只凭挂载卡 excerpt 下结论。"
                        )
                    )
                    dump_rule = ""
                    if wants_document_content(message):
                        dump_rule = (
                            "\n【用户意图】对方明确要求查看附件完整内容："
                            "请按已注入正文结构化输出（可分节），"
                            "禁止再要求用户说「开始读简历」，禁止改成求职咨询三选项菜单。"
                        )
                    sys_prompt = (
                        f"{prefix}\n\n{completeness}{dump_rule}\n\n"
                        f"{get_ask_thinking_instruction()}"
                    )
                    user_block = (
                        "以下是本轮已挂载附件（经文档访问层组装，含完整性标记）：\n\n"
                        + doc_pack
                        + f"\n\n用户问题：{message or '请基于附件给出简要职业建议'}"
                    )
                    # Oversized docs: allow pagination tool after first pack
                    if not pack_report.get("all_complete"):
                        path = "ask_attachments_docs_paged"
                        from .scoped_invoke import scoped_invoke
                        from .task_packet import build_standard_packet

                        packet = build_standard_packet(
                            subagent="qa",
                            goal=(
                                (message or "请基于已挂载附件回答")
                                + "\n\n"
                                + completeness
                                + "\n\n已注入章节目录+首页窗口（勿再截断）。"
                                "策略：先 catalog/已给 TOC → 问题相关用 "
                                "attachment_read mode=find(query=用户问题) 或 "
                                "mode=section；画像级完整结论再用 mode=read "
                                "从 offset=0 至 complete=true。find/section "
                                "均为 coverage=partial：\n"
                                + doc_pack
                            ),
                            user_utterance=message,
                            write_keys=[],
                        )
                        out = scoped_invoke(
                            packet,
                            blackboard,
                            allow_tools=True,
                            interaction_mode=InteractionMode.ASK,
                            session_id=session_id,
                            student_id=student_id,
                            plan_tool_allowlist={"attachment_read"},
                            temperature=0.45,
                            max_tokens=1800,
                        )
                        reply = out.get("reply") or ""
                        tool_calls = out.get("tool_calls") or []
                        denials = out.get("permission_denials") or []
                    else:
                        raw = get_llm_client().chat(
                            messages=[
                                {"role": "system", "content": sys_prompt},
                                {"role": "user", "content": user_block},
                            ],
                            temperature=0.45,
                            max_tokens=1800,
                        ) or ""
                        steps, answer = parse_thinking_answer(raw)
                        if answer:
                            reply = answer
                            thinking_steps = steps
                        else:
                            reply = raw.strip()
                            thinking_steps = []
            except Exception as e:
                logger.info("ask document pack path failed, fallback tools: %s", e)
                reply = ""
                thinking_steps = []

            if not (reply or "").strip():
                from .scoped_invoke import scoped_invoke
                from .task_packet import build_standard_packet

                path = "ask_attachments_tools"
                packet = build_standard_packet(
                    subagent="qa",
                    goal=(
                        (message or "请基于已挂载附件回答")
                        + "。先 attachment_read mode=catalog 看章节；"
                        "能定位则 mode=find(query=…) 或 mode=section；"
                        "需要完整画像结论再 mode=read 分页至 complete=true。"
                        "禁止只凭首页或 find 局部命中下全文结论。"
                    ),
                    user_utterance=message,
                    write_keys=[],
                )
                out = scoped_invoke(
                    packet,
                    blackboard,
                    allow_tools=True,
                    interaction_mode=InteractionMode.ASK,
                    session_id=session_id,
                    student_id=student_id,
                    plan_tool_allowlist={"attachment_read"},
                    temperature=0.45,
                    max_tokens=1800,
                )
                reply = out.get("reply") or ""
                tool_calls = out.get("tool_calls") or []
                denials = out.get("permission_denials") or []
                thinking_steps = []

            filenames: List[str] = []
            for fmeta in (pack_report.get("files") or []):
                if isinstance(fmeta, dict) and fmeta.get("filename"):
                    filenames.append(str(fmeta["filename"]))
            if not filenames:
                try:
                    from .media.file_store import get_agent_file_service
                    store = get_agent_file_service()
                    for fid in turn_file_ids[:4]:
                        meta = store.get(fid) or {}
                        if meta.get("filename"):
                            filenames.append(str(meta["filename"]))
                except Exception:
                    pass
            if not thinking_steps:
                thinking_steps = ask_fallback_trace(
                    message,
                    path="ask_attachments",
                    filenames=filenames,
                    mount_ok=not bool(mount_errors),
                )
            else:
                thinking_steps = ensure_min_depth(
                    thinking_steps,
                    fallback=ask_fallback_trace(
                        message,
                        path="ask_attachments",
                        filenames=filenames,
                        mount_ok=not bool(mount_errors),
                    ),
                    min_n=4,
                )

            if mount_errors:
                reply = f"（附件挂载警告: {mount_errors[0]}）\n{reply}"
            return self._pack(
                reply=reply,
                session_id=session_id,
                start=start,
                active_subagent="ask",
                strategy=(
                    "document_content_dump"
                    if path == "ask_document_dump"
                    else "ask_with_attachments"
                ),
                blackboard=blackboard,
                tool_calls=tool_calls,
                memory_mounted=bool(memory_mount),
                upgrade_hint=upgrade_hint,
                supervisor_view={
                    "path": path,
                    "file_ids": turn_file_ids,
                    "filenames": filenames,
                    "mount_errors": mount_errors or [],
                    "doc_pack": {
                        "all_complete": pack_report.get("all_complete"),
                        "files": [
                            {
                                "file_id": x.get("file_id"),
                                "total_chars": x.get("total_chars"),
                                "fits_inline": x.get("fits_inline"),
                            }
                            for x in (pack_report.get("files") or [])
                            if isinstance(x, dict)
                        ],
                    },
                },
                permission_denials=denials,
                thinking_steps=thinking_steps,
                message=message,
            )

        # Narrow Ask accel: greetings + capability probes (no LTM bleed)
        if is_pure_greeting(message) or is_capability_probe(message):
            reply = greeting_reply(message)
            if upgrade_hint:
                reply += f"\n\n提示：{upgrade_hint.get('reason', '')}可切换到 {upgrade_hint.get('to')} 模式。"
            path = "ask_capability" if is_capability_probe(message) else "ask_greeting"
            return self._pack(
                reply=reply,
                session_id=session_id,
                start=start,
                active_subagent="ask",
                strategy="ask",
                blackboard=blackboard,
                memory_mounted=bool(memory_mount),
                upgrade_hint=upgrade_hint,
                supervisor_view={"path": path},
                thinking_steps=ask_fallback_trace(message, path=path),
                message=message,
            )

        # Strong task intent under Ask: explain + upgrade_hint without paying Agent cost
        if upgrade_hint:
            reply = (
                f"当前是 Ask 模式，我可以解释思路，但不能直接调用业务工具执行。"
                f"{upgrade_hint.get('reason', '')}"
                f"请切换到 **{upgrade_hint.get('to')}** 模式后再发送同样的请求。"
            )
            return self._pack(
                reply=reply,
                session_id=session_id,
                start=start,
                active_subagent="ask",
                strategy="ask",
                blackboard=blackboard,
                tool_calls=[],
                memory_mounted=bool(memory_mount),
                upgrade_hint=upgrade_hint,
                supervisor_view={"path": "ask_upgrade_hint", "history_messages": len(hist)},
                thinking_steps=ask_fallback_trace(
                    message,
                    path="ask_upgrade_hint",
                    upgrade_to=str(upgrade_hint.get("to") or "Agent"),
                ),
                message=message,
            )

        from ...utils.llm_client import get_llm_client

        llm = get_llm_client()
        system_base = assembled_context.system_prompt if assembled_context else prefix
        system = f"{system_base}\n\n{get_ask_thinking_instruction()}"
        llm_messages = [{"role": "system", "content": system}]
        if assembled_context:
            llm_messages.extend(assembled_context.messages)
        else:
            llm_messages.append({"role": "user", "content": message})
        raw = llm.chat(
            messages=llm_messages,
            temperature=0.5,
            max_tokens=1800,
        ) or ""
        steps, answer = parse_thinking_answer(raw)
        if answer:
            reply = answer
        else:
            # Model ignored the scratchpad contract — keep usable reply, deepen via fallback.
            reply = raw.strip() or (
                "我在 Ask 模式，可以解答职业规划问题。"
                "需要执行画像/匹配/报告请切换到 Agent 或 Experts。"
            )
            steps = []
        steps = ensure_min_depth(
            steps,
            fallback=ask_fallback_trace(message, path="ask_llm"),
            min_n=5,
        )

        return self._pack(
            reply=reply,
            session_id=session_id,
            start=start,
            active_subagent="ask",
            strategy="ask",
            blackboard=blackboard,
            tool_calls=[],
            memory_mounted=bool(memory_mount),
            upgrade_hint=upgrade_hint,
            supervisor_view={
                "path": "ask_llm",
                "history_messages": len(assembled_context.messages) if assembled_context else len(hist),
            },
            thinking_steps=steps,
            message=message,
        )

    def _run_agent(
        self,
        message: str,
        blackboard: Blackboard,
        summary: str,
        card: str,
        start: float,
        session_id: Optional[str],
        memory_mount: str,
        budget,
        student_id: Optional[str],
        turn_file_ids: Optional[List[str]] = None,
        mount_errors: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        # Mounted docs: materialize like Ask — Agent must not stall on「开始读简历」.
        if turn_file_ids and not (mount_errors or []):
            try:
                from .media.document_access import materialize_for_ask

                doc_pack, pack_report = materialize_for_ask(
                    list(turn_file_ids[:4]),
                    query=message or "",
                    student_id=student_id,
                    session_id=session_id,
                )
                logger.info(
                    "agent materialize files=%s all_complete=%s pack_chars=%s",
                    len(turn_file_ids[:4]),
                    pack_report.get("all_complete"),
                    len(doc_pack or ""),
                )
                if (
                    doc_pack.strip()
                    and wants_document_content(message)
                    and pack_report.get("all_complete")
                ):
                    return self._pack(
                        reply=(
                            "已读取本轮挂载简历的完整正文（电话/邮箱已脱敏）。\n\n"
                            + doc_pack
                        ),
                        session_id=session_id,
                        start=start,
                        active_subagent="qa_subagent",
                        strategy="document_content_dump",
                        blackboard=blackboard,
                        memory_mounted=bool(memory_mount),
                        supervisor_view={
                            "path": "agent_document_dump",
                            "file_ids": turn_file_ids,
                            "doc_pack": {
                                "all_complete": pack_report.get("all_complete"),
                            },
                        },
                        thinking_steps=[
                            "用户要的是附件完整内容，不是求职咨询菜单。",
                            "文档访问层已标记 all_complete=true，正文已在服务端物化。",
                            "本轮直接返回脱敏全文，不再要求用户说「开始读简历」。",
                            "若还需优劣势梳理或岗位对照，可在下一轮说明目标方向。",
                        ],
                        message=message,
                    )
                if doc_pack.strip():
                    completeness = (
                        "【附件完整性】以下材料均为全文（complete=true），必须按全文作答；"
                        "挂载卡 excerpt 不是正文。"
                        if pack_report.get("all_complete")
                        else (
                            "【附件完整性】部分材料超过单次窗口；优先 attachment_read "
                            "mode=find/section；禁止只凭挂载卡 excerpt 下结论。"
                        )
                    )
                    dump_rule = ""
                    if wants_document_content(message):
                        dump_rule = (
                            "【用户意图】对方明确要求查看附件完整内容："
                            "请按已注入正文结构化输出，禁止再要求用户说「开始读简历」，"
                            "禁止改成求职咨询三选项菜单。"
                        )
                    goal = (
                        (message or "请基于已挂载附件回答")
                        + "\n\n"
                        + completeness
                        + (("\n" + dump_rule) if dump_rule else "")
                        + "\n\n已注入附件正文：\n"
                        + doc_pack
                    )
                    use_tools = bool(budget.allow_tools) and (
                        not pack_report.get("all_complete") or qa_needs_tools(message)
                    )
                    packet = build_standard_packet(
                        subagent="qa",
                        goal=goal,
                        user_utterance=message,
                        write_keys=[],
                    )
                    out = _invoke_subagent(
                        packet,
                        blackboard,
                        session_id,
                        allow_tools=use_tools,
                        interaction_mode=InteractionMode.AGENT,
                        student_id=student_id,
                    )
                    return self._pack(
                        reply=out.get("reply") or "已完成处理。",
                        session_id=session_id,
                        start=start,
                        active_subagent="qa_subagent",
                        strategy="agent_with_attachments",
                        blackboard=blackboard,
                        tool_calls=out.get("tool_calls") or [],
                        memory_mounted=bool(memory_mount),
                        supervisor_view={
                            "path": "agent_attachments",
                            "allow_tools": use_tools,
                            "file_ids": turn_file_ids,
                            "doc_pack": {
                                "all_complete": pack_report.get("all_complete"),
                            },
                        },
                        permission_denials=out.get("permission_denials") or [],
                        message=message,
                    )
            except Exception as e:
                logger.info("agent document pack path failed, fallback route: %s", e)

        keyword_sub = keyword_route(message)
        subagent = keyword_sub or "qa"
        goal = message
        action = "call"
        confidence = 0.8 if keyword_sub else 0.6

        if budget.allow_llm_router and should_use_llm_router(message, keyword_sub):
            decision = _llm_route(
                message,
                summary,
            )
            if decision:
                action = decision.get("action") or "call"
                confidence = float(decision.get("confidence") or 0.5)
                if decision.get("subagent") in ("profile", "match", "report", "planner", "qa"):
                    if confidence >= 0.55 or not keyword_sub:
                        subagent = decision["subagent"]
                goal = decision.get("goal") or message
                if action == "clarify":
                    return self._pack(
                        reply=decision.get("clarify_question")
                        or "请确认你是想做画像、匹配、报告还是学习规划？",
                        session_id=session_id,
                        start=start,
                        active_subagent="supervisor",
                        strategy="agent_clarify",
                        blackboard=blackboard,
                        memory_mounted=bool(memory_mount),
                        message=message,
                    )

        gate_err = blackboard.check_gate(subagent)
        if gate_err and subagent != "qa":
            return self._pack(
                reply=f"{gate_err}。请先补齐前置步骤，或改用 Experts 一键链路。",
                session_id=session_id,
                start=start,
                active_subagent="supervisor",
                strategy="gate_block",
                blackboard=blackboard,
                memory_mounted=bool(memory_mount),
                message=message,
            )

        # Attachments force tools even when keyword_route missed content intent
        use_tools = budget.allow_tools and (
            subagent != "qa"
            or qa_needs_tools(message)
            or bool(turn_file_ids)
        )
        packet = build_standard_packet(
            subagent=subagent,
            goal=goal,
            user_utterance=message,
            write_keys=[] if subagent == "qa" or not budget.allow_blackboard_business_write else None,
        )
        out = _invoke_subagent(
            packet,
            blackboard,
            session_id,
            allow_tools=use_tools,
            interaction_mode=InteractionMode.AGENT,
            student_id=student_id,
        )
        committed = {}
        if budget.allow_blackboard_business_write:
            committed = commit_step_updates(
                blackboard,
                out.get("blackboard_updates") or {},
                subagent,
                packet.write_keys,
            )
            if not committed and out.get("tool_calls"):
                from .plan import _maybe_heuristic_commit
                _maybe_heuristic_commit(blackboard, packet, out, committed)

        return self._pack(
            reply=out.get("reply") or "已完成处理。",
            session_id=session_id,
            start=start,
            active_subagent=f"{subagent}_subagent",
            strategy="agent",
            blackboard=blackboard,
            tool_calls=out.get("tool_calls") or [],
            memory_mounted=bool(memory_mount),
            supervisor_view={"allow_tools": use_tools, "confidence": confidence},
            permission_denials=out.get("permission_denials") or [],
            message=message,
        )

    def _run_experts(
        self,
        message: str,
        blackboard: Blackboard,
        summary: str,
        card: str,
        start: float,
        session_id: Optional[str],
        plan_mode: bool,
        memory_mount: str,
        student_id: Optional[str],
    ) -> Dict[str, Any]:
        # LeadIntentPacket: understand → clarify slots → run_plan (never keyword-auto).
        decision = experts_lead_decide(
            message,
            card=card,
            summary=summary,
            memory_digest=memory_mount,
            plan_mode=bool(plan_mode),
            blackboard=blackboard,
        )
        action = decision.get("action") or "clarify"
        lead_view = {
            "lead_action": action,
            "lead_source": decision.get("source"),
            "lead_confidence": decision.get("confidence"),
            "user_job": decision.get("user_job"),
            "goal_summary": decision.get("goal_summary"),
            "missing_slots": decision.get("missing_slots") or [],
            "plan_sketch": decision.get("plan_sketch") or [],
            "clarify_options": decision.get("clarify_options") or [],
            "vagueness": decision.get("vagueness"),
        }
        if action in ("explain", "clarify"):
            return self._pack(
                reply=str(decision.get("reply") or ""),
                session_id=session_id,
                start=start,
                active_subagent="lead",
                strategy="experts_clarify",
                blackboard=blackboard,
                memory_mounted=bool(memory_mount),
                supervisor_view=lead_view,
                message=message,
            )

        template = decision.get("template")
        if not template:
            return self._pack(
                reply="还需要更明确的目标，我才能启动多专家计划。",
                session_id=session_id,
                start=start,
                active_subagent="lead",
                strategy="experts_clarify",
                blackboard=blackboard,
                memory_mounted=bool(memory_mount),
                supervisor_view=lead_view,
                message=message,
            )

        packets = build_plan_packets(
            str(template),
            message,
            handoff_brief=str(decision.get("handoff_brief") or ""),
            goal_summary=str(decision.get("goal_summary") or ""),
        )
        all_denials: List[Dict[str, str]] = []

        def _step(p, bb):
            allow = set(tools_for(p.subagent))
            out = _invoke_subagent(
                p,
                bb,
                session_id,
                allow_tools=True,
                interaction_mode=InteractionMode.EXPERTS,
                student_id=student_id,
                plan_tool_allowlist=allow,
            )
            all_denials.extend(out.get("permission_denials") or [])
            return out

        results, narrative = run_plan(packets, blackboard, _step)
        active = results[-1].subagent if results else "lead"
        # Prefixed user-visible sketch (DeepSeek/Kimi: short plan, not raw CoT)
        sketch = decision.get("plan_sketch") or []
        if sketch and isinstance(sketch, list):
            head = "计划草案:\n" + "\n".join(str(s) for s in sketch)
            narrative = head + "\n\n" + narrative
        lead_view.update({
            "plan_template": template,
            "lead_action": "run_plan",
            "summary_chars": len(summary),
        })
        return self._pack(
            reply=narrative,
            session_id=session_id,
            start=start,
            active_subagent=f"{active}_subagent",
            strategy="experts",
            blackboard=blackboard,
            plan_results=[{
                "subagent": r.subagent, "ok": r.ok, "error": r.error,
            } for r in results],
            memory_mounted=bool(memory_mount),
            supervisor_view=lead_view,
            permission_denials=all_denials,
            message=message,
        )


_runtime: Optional[ModeRuntime] = None
_runtime_lock = threading.Lock()


def get_mode_runtime() -> ModeRuntime:
    global _runtime
    if _runtime is None:
        with _runtime_lock:
            if _runtime is None:
                _runtime = ModeRuntime()
    return _runtime
