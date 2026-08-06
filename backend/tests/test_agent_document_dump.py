"""Agent/Ask must return mounted resume body when user asks for 完整内容."""

from __future__ import annotations

from app.services.agent_runtime.blackboard import Blackboard
from app.services.agent_runtime.mode_runtime import ModeRuntime
from app.services.agent_runtime.modes import budget_for, InteractionMode


def test_agent_dumps_complete_resume_without_stall(monkeypatch):
    pack = (
        "=== file_id=f1 filename=resume.pdf complete=true ===\n"
        "姓名：测试\n教育经历\n某某大学\n项目经验\n示例项目\n"
    )

    def _fake_materialize(file_ids, query="", **_kwargs):
        return pack, {"all_complete": True, "files": [{"file_id": "f1", "filename": "resume.pdf"}]}

    monkeypatch.setattr(
        "app.services.agent_runtime.media.document_access.materialize_for_ask",
        _fake_materialize,
    )

    rt = ModeRuntime()
    out = rt._run_agent(
        "这篇简历的完整内容",
        Blackboard(),
        summary="",
        card="",
        start=0.0,
        session_id="s1",
        memory_mount="",
        budget=budget_for(InteractionMode.AGENT),
        student_id=None,
        turn_file_ids=["f1"],
        mount_errors=[],
    )
    assert out["orchestration"]["strategy"] == "document_content_dump"
    assert "开始读简历" not in out["reply"]
    assert "教育经历" in out["reply"]
    assert "某某大学" in out["reply"]


def test_ask_dumps_complete_resume_without_stall(monkeypatch):
    pack = (
        "=== file_id=f1 filename=resume.pdf complete=true ===\n"
        "姓名：测试\n实习经历\n某公司\n"
    )

    def _fake_materialize(file_ids, query="", **_kwargs):
        return pack, {"all_complete": True, "files": [{"file_id": "f1", "filename": "resume.pdf"}]}

    monkeypatch.setattr(
        "app.services.agent_runtime.media.document_access.materialize_for_ask",
        _fake_materialize,
    )

    rt = ModeRuntime()
    out = rt._run_ask(
        "这篇简历的完整内容",
        Blackboard(),
        prefix="system",
        hist=[],
        start=0.0,
        session_id="s1",
        upgrade_hint=None,
        memory_mount="",
        turn_file_ids=["f1"],
        mount_errors=[],
    )
    assert out["orchestration"]["strategy"] == "document_content_dump"
    assert "开始读简历" not in out["reply"]
    assert "实习经历" in out["reply"]


def test_subagent_task_body_not_in_system_packet_json():
    from app.services.agent_runtime.scoped_invoke import _build_subagent_system
    from app.services.agent_runtime.task_packet import build_standard_packet

    far_marker = "UNIQUE_BODY_MARKER_SHOULD_STAY_IN_USER_CHANNEL"
    long_body = "文档正文：" + ("完整简历内容" * 80) + far_marker
    packet = build_standard_packet(
        subagent="qa",
        goal=long_body,
        user_utterance="请分析这份简历",
        write_keys=[],
    )
    system = _build_subagent_system(
        "qa",
        packet,
        {},
        interaction_mode=InteractionMode.AGENT,
    )

    assert "TaskPacket" in system
    assert long_body not in system
    assert far_marker not in system
