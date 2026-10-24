"""Prompt-budget and multi-attachment coverage at the model-call boundary."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.agent_runtime.blackboard import Blackboard
from app.services.agent_runtime.context_assembler import AssembledContext
from app.services.agent_runtime.media import document_access
from app.services.agent_runtime.mode_runtime import ModeRuntime
from app.services.agent_runtime.modes import InteractionMode, budget_for


def test_complete_inline_resume_does_not_mount_a_second_copy_through_tools(monkeypatch):
    seen = {}
    monkeypatch.setattr(document_access, 'materialize_for_ask', lambda *_a, **_k: ('FULL_RESUME_BODY', {'all_complete': True, 'files': []}))
    monkeypatch.setattr('app.services.agent_runtime.mode_runtime._llm_route', lambda *_a, **_k: {'document_view': 'answer'})
    def invoke(*args, **kwargs):
        seen.update(kwargs)
        return {'reply': '有依据的简历分析'}
    monkeypatch.setattr('app.services.agent_runtime.mode_runtime._invoke_subagent', invoke)
    ModeRuntime()._run_agent('分析简历，不复述原文', Blackboard(), '', '', 0, 'session', '',
                            budget_for(InteractionMode.AGENT), 'owner', turn_file_ids=['file_owned'], mount_errors=[])
    assert seen['allow_tools'] is False


def test_partial_document_only_receives_document_read_tools(monkeypatch):
    seen = {}
    monkeypatch.setattr(document_access, 'materialize_for_ask', lambda *_a, **_k: ('PARTIAL', {'all_complete': False, 'files': []}))
    def invoke(*args, **kwargs):
        seen.update(kwargs)
        return {'reply': '读取仍不完整', 'incomplete': True, 'stop_reason': 'turn_token_cap_exceeded'}
    monkeypatch.setattr('app.services.agent_runtime.mode_runtime._invoke_subagent', invoke)
    result = ModeRuntime()._run_agent('分析简历', Blackboard(), '', '', 0, 'session', '',
                                     budget_for(InteractionMode.AGENT), 'owner', turn_file_ids=['file_owned'], mount_errors=[])
    assert seen['plan_tool_allowlist'] == {'attachment_read'}
    assert result['incomplete'] is True


class _Files:
    def __init__(self, count: int, chars: int):
        self.bodies = {f"file_{i}": f"MARKER_{i}\n" + ("文档内容。" * chars) for i in range(count)}
        self.scheduled = []

    def get(self, file_id):
        return {"file_id": file_id, "filename": f"resume_{file_id}.txt", "status": "ready"}

    def list_artifacts(self, file_id):
        return [{
            "kind": "full_text", "body_text": self.bodies[file_id],
            "meta": {"chars": len(self.bodies[file_id]), "truncated": False},
        }]

    def schedule_upgrade(self, file_id):
        self.scheduled.append(file_id)
        return {"upgrade_status": "ready"}


def test_eight_attachments_share_one_prompt_budget_without_silent_drop(monkeypatch):
    files = _Files(8, 1700)  # each is > 8k characters
    monkeypatch.setattr(document_access, "get_agent_file_service", lambda: files)
    ids = list(files.bodies)
    pack, report = document_access.materialize_for_ask(ids)

    assert files.scheduled == ids
    assert [f["file_id"] for f in report["files"]] == ids
    assert all(fid in pack for fid in ids)
    assert report["all_complete"] is False
    assert report["inline_budget_chars"] == 48_000
    assert report["pack_chars"] <= 48_000
    assert "MARKER_4" in pack  # fifth attachment is part of the actual prompt
    assert "complete=false" in pack
    assert "attachment_read" in pack


def test_ask_owned_attachment_card_reaches_context_after_rebuild(monkeypatch):
    from app.services.agent_runtime.media import context_mount, file_store

    files = SimpleNamespace(get=lambda _fid: {
        "file_id": "file_owned", "filename": "resume.pdf",
        "student_id": "stu_owned", "status": "ready",
    })
    monkeypatch.setattr(file_store, "get_agent_file_service", lambda: files)
    monkeypatch.setattr(context_mount, "get_agent_file_service", lambda: files)
    monkeypatch.setattr(context_mount, "list_resources", lambda *_a, **_k: [])
    monkeypatch.setattr("app.services.db.get_mysql_client", lambda: None)
    monkeypatch.setattr(
        "app.services.agent_runtime.evidence_retrieval.build_evidence_pack",
        lambda *_a, **_k: {"status": "empty", "items": [], "sources": [], "continuations": []},
    )
    captured = {}

    def run_ask(self, *_args, assembled_context=None, **_kwargs):
        captured["system_prompt"] = assembled_context.system_prompt
        return {"reply": "已读取附件目录", "orchestration": {}}

    monkeypatch.setattr(ModeRuntime, "_run_ask", run_ask)
    prepared = {
        "visible_text": "请分析简历", "parts": [{"type": "document", "file_id": "file_owned"}],
        "turn_file_ids": ["file_owned"], "attach_card": "", "mount_errors": [],
    }
    ModeRuntime().chat(
        "请分析简历", session_id="sess_owned", student_id="stu_owned",
        interaction_mode="ask", prepared_turn=prepared,
    )
    assert "file_owned" in captured["system_prompt"]
    assert "attachment_read" in captured["system_prompt"]


def test_ask_explicit_reference_exposes_only_current_owner_session_mount(monkeypatch):
    from app.services.agent_runtime.media import session_attachment_context, session_resources

    monkeypatch.setattr(session_resources, "list_resources", lambda sid, *, student_id: [
        {"session_id": sid, "student_id": student_id, "file_id": "file_resume"},
    ])
    assert session_attachment_context.ask_attachment_read_scope(
        "请继续看我刚才上传的简历内容",
        session_id="session_current", student_id="owner_current",
    ) == ["file_resume"]
    assert session_attachment_context.ask_attachment_read_scope(
        "你好，继续刚才的话题",
        session_id="session_current", student_id="owner_current",
    ) == []
    assert session_attachment_context.ask_attachment_read_scope(
        "请继续看这份简历",
        session_id="", student_id="owner_current",
    ) == []
    assert session_attachment_context.ask_attachment_read_scope(
        "Please read the attached file again",
        session_id="session_current", student_id="owner_current",
    ) == ["file_resume"]
    assert session_attachment_context.ask_attachment_read_scope(
        "Please resume the earlier plan",
        session_id="session_current", student_id="owner_current",
    ) == []


def test_attachment_reader_requires_mount_in_bound_owner_session(monkeypatch):
    from app.services.agent_runtime.media import document_access, file_store, session_resources
    from app.services.agent_runtime.media.types import CHARS_READ_PAGE
    from app.services.tools.attachment_read import create_attachment_read_tool

    reads = []
    resource = {"session_id": "session_current", "student_id": "owner_current",
                "file_id": "file_resume"}
    monkeypatch.setattr(
        session_resources, "list_resources",
        lambda sid, *, student_id: (
            [resource] if sid == "session_current" and student_id == "owner_current" else []
        ),
    )
    monkeypatch.setattr(
        file_store, "get_agent_file_service",
        lambda: SimpleNamespace(get=lambda file_id: {
            "file_id": file_id, "filename": "resume.txt", "status": "ready",
            "student_id": "owner_current",
        }),
    )

    def read_window(file_id, *, offset, max_chars):
        reads.append((file_id, offset, max_chars))
        return document_access.DocWindow(
            file_id=file_id, filename="resume.txt", total_chars=24, offset=offset,
            text="SYNTHETIC_RESUME: experience", next_offset=None,
            has_more=False, complete=True, coverage="full",
        )

    monkeypatch.setattr(document_access, "read_window", read_window)
    tool = create_attachment_read_tool(student_id="owner_current", session_id="session_current")

    result = tool.invoke({"file_id": "file_resume", "mode": "read"})
    denied = tool.invoke({"file_id": "file_other_session", "mode": "read"})

    assert "SYNTHETIC_RESUME: experience" in result
    assert "complete=true" in result
    assert denied == "[attachment_read] 文件未挂载到当前会话，拒绝读取"
    assert reads == [("file_resume", 0, CHARS_READ_PAGE)]


def test_ask_followup_mount_card_and_attachment_tool_stay_session_scoped(monkeypatch):
    from app.services.agent_runtime import quality_gate, scoped_invoke, task_packet
    from app.services.agent_runtime.media import context_mount, file_store, session_resources
    from app.services.agent_runtime.mode_runtime import ModeRuntime

    resource = {
        "session_id": "sess_old_file", "file_id": "file_resume",
        "filename": "resume.txt", "mount_path": "/data/file_resume",
    }
    files = SimpleNamespace(get=lambda _fid: {
        "file_id": "file_resume", "filename": "resume.txt",
        "student_id": "stu_owner", "status": "ready",
    })
    monkeypatch.setattr(file_store, "get_agent_file_service", lambda: files)
    monkeypatch.setattr(context_mount, "get_agent_file_service", lambda: files)
    monkeypatch.setattr(session_resources, "list_resources", lambda *_a, **_k: [resource])
    monkeypatch.setattr(context_mount, "list_resources", lambda *_a, **_k: [resource])
    monkeypatch.setattr("app.services.db.get_mysql_client", lambda: None)

    class EmptySessionManager:
        def get_session(self, _sid):
            return None

    monkeypatch.setattr(
        "app.services.session_manager.get_session_manager",
        lambda: EmptySessionManager(),
    )
    monkeypatch.setattr(
        "app.services.agent_runtime.evidence_retrieval.build_evidence_pack",
        lambda *_a, **_k: {
            "status": "empty", "items": [], "sources": [], "continuations": [],
            "coverage": {"reason": "evidence_not_needed", "complete": True},
        },
    )

    observed = {}

    def invoke(packet, _blackboard, **kwargs):
        observed["packet"] = packet
        observed.update(kwargs)
        return {"reply": "根据当前会话附件回答", "tool_calls": []}

    monkeypatch.setattr(scoped_invoke, "scoped_invoke", invoke)
    monkeypatch.setattr(task_packet, "build_standard_packet", lambda **kwargs: kwargs)
    monkeypatch.setattr("app.utils.llm_client.get_llm_client", lambda: object())
    monkeypatch.setattr(
        "app.services.agent_runtime.mode_runtime._ask_answer_or_repair",
        lambda *_a, **_k: ([], "根据当前会话附件回答"),
    )
    monkeypatch.setattr(
        quality_gate, "ground_evidence_answer",
        lambda _llm, reply, *_a, **_k: (reply, {"status": "not_applicable"}),
    )
    monkeypatch.setattr(
        "app.services.agent_runtime.mode_runtime._evidence_summary_part",
        lambda *_a, **_k: None,
    )
    saved_resume_pack = {
        "status": "completed",
        "answer_kind": "source_readback",
        "items": [{
            "uri": "career://me/resume-reports/resume_0123456789abcdef0123456789abcdef",
            "section": "analysis", "analysis_content_status": "current", "version": "v1",
            "data": {"kind": "summary", "text": "已保存的结构化简历总结"},
        }],
        "coverage": {"reason": "complete", "complete": True},
    }

    result = ModeRuntime()._run_ask(
        "请继续看我刚才上传的简历内容",
        Blackboard(), prefix="system", hist=[], start=0.0,
        session_id="sess_old_file", upgrade_hint=None, memory_mount="",
        student_id="stu_owner", turn_file_ids=[], session_attachment_ids=["file_resume"],
        mount_errors=[],
        evidence_pack=saved_resume_pack,
    )
    assert result["reply"] == "根据当前会话附件回答"
    assert observed["allow_tools"] is True
    assert observed["plan_tool_allowlist"] == {"attachment_read"}
    assert observed["session_id"] == "sess_old_file"
    assert observed["student_id"] == "stu_owner"
    assert "当前会话已挂载且归属当前账号" in observed["packet"]["success_criteria"]


def test_ask_chat_includes_explicitly_referenced_prior_mount_in_context(monkeypatch):
    from app.services.agent_runtime.media import context_mount, file_store, session_resources

    resource = {
        "session_id": "sess_old_file", "file_id": "file_resume",
        "filename": "resume.txt", "mount_path": "/data/file_resume",
    }
    files = SimpleNamespace(get=lambda _fid: {
        "file_id": "file_resume", "filename": "resume.txt",
        "student_id": "stu_owner", "status": "ready",
    })
    monkeypatch.setattr(file_store, "get_agent_file_service", lambda: files)
    monkeypatch.setattr(context_mount, "get_agent_file_service", lambda: files)
    monkeypatch.setattr(session_resources, "list_resources", lambda *_a, **_k: [resource])
    monkeypatch.setattr(context_mount, "list_resources", lambda *_a, **_k: [resource])
    monkeypatch.setattr("app.services.db.get_mysql_client", lambda: None)

    class EmptySessionManager:
        def get_session(self, _sid):
            return None

    monkeypatch.setattr(
        "app.services.session_manager.get_session_manager",
        lambda: EmptySessionManager(),
    )
    monkeypatch.setattr(
        "app.services.agent_runtime.evidence_retrieval.build_evidence_pack",
        lambda *_a, **_k: {"status": "empty", "items": [], "sources": [], "continuations": []},
    )
    captured = {}

    def run_ask(self, *_args, assembled_context=None, session_attachment_ids=None,
                 turn_file_ids=None, **_kwargs):
        captured["system_prompt"] = assembled_context.system_prompt
        captured["session_attachment_ids"] = session_attachment_ids
        captured["turn_file_ids"] = turn_file_ids
        return {"reply": "已收到当前会话附件范围", "orchestration": {}}

    monkeypatch.setattr(ModeRuntime, "_run_ask", run_ask)
    message = "请继续看我刚才上传的简历内容"
    ModeRuntime().chat(
        message, session_id="sess_old_file", student_id="stu_owner",
        interaction_mode="ask",
        prepared_turn={
            "visible_text": message,
            "parts": [{"type": "text", "text": message}],
            "turn_file_ids": [], "attach_card": "", "mount_errors": [],
        },
    )
    assert captured["turn_file_ids"] == []
    assert captured["session_attachment_ids"] == ["file_resume"]
    assert "当前会话已挂载附件" in captured["system_prompt"]
    assert "[会话内] resume.txt" in captured["system_prompt"]
    assert "attachment_read" in captured["system_prompt"]


def test_eight_small_attachments_reach_model_with_complete_text(monkeypatch):
    files = _Files(8, 30)
    monkeypatch.setattr(document_access, "get_agent_file_service", lambda: files)
    pack, report = document_access.materialize_for_ask(list(files.bodies))

    assert report["all_complete"] is True
    assert report["pack_chars"] <= 48_000
    for i in range(8):
        assert f"MARKER_{i}" in pack
        assert report["files"][i]["prompt_complete"] is True


def test_small_shared_budget_keeps_all_eight_file_identities(monkeypatch):
    files = _Files(8, 1700)
    monkeypatch.setattr(document_access, "get_agent_file_service", lambda: files)
    pack, report = document_access.pack_for_prompt(list(files.bodies), inline_budget=3000)
    assert len(pack) <= 3000
    assert report["all_complete"] is False
    for fid in files.bodies:
        assert f"file_id={fid}" in pack


def test_pending_ocr_repair_never_advertises_existing_thin_text_as_full(monkeypatch):
    files = _Files(1, 20)
    original_get = files.get
    files.get = lambda fid: {**original_get(fid), "upgrade_status": "queued"}
    files.schedule_upgrade = lambda fid: files.get(fid)
    monkeypatch.setattr(document_access, "get_agent_file_service", lambda: files)
    pack, report = document_access.materialize_for_ask(["file_0"])
    assert report["all_complete"] is False
    assert report["files"][0]["source_complete"] is False
    assert "complete=true" not in pack
    assert "重解析状态=queued" in pack


def test_ask_sends_fifth_attachment_text_into_real_model_message(monkeypatch):
    captured = {}

    def materialize(ids, **_kwargs):
        captured["ids"] = ids
        body = "\n".join(f"FULL_{fid}" for fid in ids)
        return body, {"all_complete": True, "files": [{"file_id": fid} for fid in ids]}

    class LLM:
        provider = "qwen"
        model = "qwen-plus"

        def chat(self, **kwargs):
            captured["messages"] = kwargs["messages"]
            return "已分析"

    monkeypatch.setattr(document_access, "materialize_for_ask", materialize)
    monkeypatch.setattr("app.utils.llm_client.get_llm_client", lambda: LLM())

    result = ModeRuntime()._run_ask(
        "请分析这些简历", Blackboard(), prefix="system", hist=[], start=0.0,
        session_id="s1", upgrade_hint=None, memory_mount="",
        turn_file_ids=[f"f{i}" for i in range(5)], mount_errors=[],
    )
    assert captured["ids"] == [f"f{i}" for i in range(5)]
    assert "FULL_f4" in captured["messages"][1]["content"]
    assert result["orchestration"]["supervisor_view"]["doc_pack"]["all_complete"] is True


def test_ask_attachment_preflight_includes_bounded_conversation_history(monkeypatch):
    captured = {}

    monkeypatch.setattr(
        document_access, "materialize_for_ask",
        lambda ids, **_kwargs: ("FULL_RESUME", {
            "all_complete": True, "files": [{"file_id": ids[0]}],
        }),
    )

    class LLM:
        provider = "qwen"
        model = "qwen-plus"

        def chat(self, **kwargs):
            captured["messages"] = kwargs["messages"]
            return "已结合上轮信息分析"

    monkeypatch.setattr("app.utils.llm_client.get_llm_client", lambda: LLM())
    assembled = AssembledContext(
        system_head="unused", system_tail="【对话摘要】已讨论校招方向",
        messages=[
            {"role": "user", "content": "上轮提到 RAG 项目"},
            {"role": "assistant", "content": "请上传简历"},
            {"role": "user", "content": "请继续分析"},
        ],
        router_context="", memory_digest="", attachment_context="",
    )
    ModeRuntime()._run_ask(
        "请继续分析", Blackboard(), prefix="unused", hist=[], start=0.0,
        session_id="s1", upgrade_hint=None, memory_mount="",
        turn_file_ids=["f1"], mount_errors=[], assembled_context=assembled,
    )
    assert [m["role"] for m in captured["messages"]] == ["system", "user", "assistant", "user"]
    assert "上轮提到 RAG 项目" in captured["messages"][1]["content"]
    assert "FULL_RESUME" in captured["messages"][-1]["content"]


def test_agent_sends_fifth_attachment_text_to_subagent(monkeypatch):
    captured = {}

    def materialize(ids, **_kwargs):
        captured["ids"] = ids
        return "\n".join(f"FULL_{fid}" for fid in ids), {
            "all_complete": True, "files": [{"file_id": fid} for fid in ids],
        }

    def invoke(packet, *_args, **_kwargs):
        captured["goal"] = packet.goal
        return {"reply": "已分析", "tool_calls": []}

    monkeypatch.setattr(document_access, "materialize_for_ask", materialize)
    monkeypatch.setattr("app.services.agent_runtime.mode_runtime._invoke_subagent", invoke)

    result = ModeRuntime()._run_agent(
        "请分析这些简历", Blackboard(), summary="", card="", start=0.0,
        session_id="s1", memory_mount="", budget=budget_for(InteractionMode.AGENT),
        student_id=None, turn_file_ids=[f"f{i}" for i in range(5)], mount_errors=[],
    )
    assert captured["ids"] == [f"f{i}" for i in range(5)]
    assert "FULL_f4" in captured["goal"]
    assert result["orchestration"]["strategy"] == "agent_with_attachments"


@pytest.mark.parametrize("fits", [False, None])
def test_ask_rejects_full_inline_when_provider_window_not_proven(monkeypatch, fits):
    captured = {}

    def materialize(ids, **_kwargs):
        return "FULL_SECRET_MARKER", {
            "all_complete": True,
            "files": [{"file_id": fid, "filename": f"resume_{fid}.txt", "status": "ready"}
                      for fid in ids],
        }

    class LLM:
        provider = "moonshot"
        model = "moonshot-v1-8k"

        def chat(self, **_kwargs):
            raise AssertionError("oversized full pack must not be sent directly")

    def paged(packet, *_args, **_kwargs):
        captured["goal"] = packet.goal
        return {"reply": "我会分段读取后回答。", "tool_calls": []}

    monkeypatch.setattr(document_access, "materialize_for_ask", materialize)
    monkeypatch.setattr("app.utils.llm_client.get_llm_client", lambda: LLM())
    monkeypatch.setattr(
        "app.services.agent_runtime.budget.context_window_status",
        lambda *_a, **_k: SimpleNamespace(
            fits=fits, estimated_input_tokens=9000, input_limit_tokens=6136,
            estimate_method="utf8_bytes_conservative_estimate", reason="estimated_input_over_window",
        ),
    )
    monkeypatch.setattr("app.services.agent_runtime.scoped_invoke.scoped_invoke", paged)

    result = ModeRuntime()._run_ask(
        "综合比较这些简历", Blackboard(), prefix="system", hist=[], start=0.0,
        session_id="s1", upgrade_hint=None, memory_mount="",
        turn_file_ids=[f"f{i}" for i in range(5)], mount_errors=[],
    )
    assert "FULL_SECRET_MARKER" not in captured["goal"]
    assert all(f"file_id=f{i}" in captured["goal"] for i in range(5))
    view = result["orchestration"]["supervisor_view"]["doc_pack"]
    assert view["all_complete"] is False
    assert view["model_inline_rejected"] is True
    assert view["model_window"]["fits"] is fits


@pytest.mark.parametrize("question,evidence,answer", [
    ("我的教育经历对校招有什么影响？", "某大学计算机专业", "教育背景能作为筛选信号，但不能仅凭学校断言录用概率。"),
    ("这个项目能证明我有 RAG 工程能力吗？", "独立完成 RAG 检索实验", "项目有相关性；还需评测数据和个人贡献来证明深度。"),
    ("我申请后端岗位，技能缺口是什么？", "Python、SQL", "简历写有 Python 和 SQL；岗位差距需对照具体 JD。"),
    ("我去上海找工作的薪资能到多少？", "未写意向城市与薪资", "材料没有薪资期望和目标岗位，先确认岗位与城市再估计。"),
])
def test_career_resume_question_receives_full_evidence_and_attachment_prompt(
    monkeypatch, question, evidence, answer,
):
    """Golden input/output contract; this does NOT grade an actual LLM's reasoning."""
    body = (
        "教育经历：某大学计算机专业。\n"
        "项目经历：独立完成 RAG 检索实验，尚未写评测指标。\n"
        "技能：Python、SQL。\n"
        "求职偏好：未写意向城市与薪资。"
    )
    files = _Files(1, 1)
    files.bodies["file_0"] = body
    monkeypatch.setattr(document_access, "get_agent_file_service", lambda: files)
    monkeypatch.setattr(document_access, "assert_document_access", lambda *_a, **_k: None)
    captured = {}

    class LLM:
        provider = "qwen"
        model = "qwen-plus"

        def chat(self, **kwargs):
            captured["messages"] = kwargs["messages"]
            return answer

    monkeypatch.setattr("app.utils.llm_client.get_llm_client", lambda: LLM())
    result = ModeRuntime()._run_ask(
        question, Blackboard(), prefix="SHOULD_NOT_REPLACE_ATTACHMENT_PROMPT",
        hist=[], start=0.0, session_id="s1", upgrade_hint=None,
        memory_mount="", turn_file_ids=["file_0"], mount_errors=[],
    )
    system, user = captured["messages"]
    assert "Ask 附件问答助手" in system["content"]
    assert "SHOULD_NOT_REPLACE_ATTACHMENT_PROMPT" not in system["content"]
    assert evidence in user["content"]
    assert "complete=true" in user["content"]
    assert question in user["content"]
    assert result["reply"] == answer
    assert result["orchestration"]["supervisor_view"]["doc_pack"]["budget_unit"] == "chars_estimate"
