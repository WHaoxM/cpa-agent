import pytest

from app.services.agent_runtime.blackboard import Blackboard
from app.services.agent_runtime.experts_lead import _infer_known_slots, _normalize, experts_lead_decide


def test_experts_chat_loads_shared_evidence_into_lead_without_granting_tools(monkeypatch):
    from app.services.agent_runtime.mode_runtime import ModeRuntime

    captured = {}
    pack = {
        "status": "partial",
        "items": [{"data": "Python"}],
        "sources": [{
            "uri": "career://me/resume", "section": "skills",
            "version": "resume-v7", "status": "completed",
            "coverage": {"complete": False, "returned": 1, "total": 20},
        }],
        "coverage": {"complete": False, "returned": 1, "total": 20},
        "has_more": True, "next_offset": 1, "truncated": True,
        "unavailable": [], "continuations": [{"next_offset": 1}],
    }

    def decide(_message, **kwargs):
        captured.update(kwargs)
        return {
            "action": "clarify", "reply": "还需确认目标岗位。",
            "source": "test", "confidence": 1.0, "user_job": "execute",
            "missing_slots": ["target_role"],
        }

    monkeypatch.setattr("app.services.db.get_mysql_client", lambda: object())
    monkeypatch.setattr(
        "app.services.agent_runtime.evidence_retrieval.build_evidence_pack",
        lambda *_a, **_k: pack,
    )
    monkeypatch.setattr("app.services.agent_runtime.mode_runtime.experts_lead_decide", decide)
    monkeypatch.setattr(
        "app.services.agent_runtime.quality_gate.turn_quality_gate",
        lambda *_a, **_k: (True, "ok"),
    )

    result = ModeRuntime().chat(
        "根据我已确认的技能做规划",
        student_id="owner-1",
        interaction_mode="experts",
        prepared_turn={
            "visible_text": "根据我已确认的技能做规划",
            "parts": [{"type": "text", "text": "根据我已确认的技能做规划"}],
            "turn_file_ids": [], "mount_errors": [], "attach_card": "",
            "verified_task_refs": [], "verified_resource_refs": [],
            "resource_overviews": [],
        },
    )

    summary = captured["summary"]
    assert "SERVER_EVIDENCE" in summary
    assert "resume-v7" in summary and '"next_offset":1' in summary
    assert "不授予业务工具或写入权限" in summary
    assert result["tool_calls"] == []
    evidence_view = result["orchestration"]["supervisor_view"]["evidence_pack"]
    assert evidence_view["has_more"] is True
    assert evidence_view["next_offset"] == 1


def test_experts_informational_evidence_turn_answers_instead_of_publishing_lead_ack(monkeypatch):
    from app.services.agent_runtime.mode_runtime import ModeRuntime

    pack = {
        "status": "partial", "items": [{"data": "Python"}, {"data": "Flask"}],
        "sources": [{"uri": "career://me/resume", "section": "skills",
                     "version": "resume-v9", "status": "completed",
                     "coverage": {"complete": False, "returned": 2, "total": 10}}],
        "coverage": {"complete": False, "returned": 2, "total": 10},
        "has_more": True, "next_offset": 2, "truncated": True,
        "unavailable": [], "continuations": [{"next_offset": 2}],
    }

    class AnswerLLM:
        def chat(self, *, messages, **_kwargs):
            prompt = messages[0]["content"]
            assert "Python" in prompt and "Flask" in prompt
            assert "resume-v9" in prompt and '"next_offset":2' in prompt
            return "前两项技能体现出 Python Web 后端方向：Python 是语言基础，Flask 是服务端框架。当前仅读取 2/10 项，版本 resume-v9；下一页 offset=2。"

    monkeypatch.setattr("app.services.db.get_mysql_client", lambda: object())
    monkeypatch.setattr("app.services.agent_runtime.evidence_retrieval.build_evidence_pack", lambda *_a, **_k: pack)
    monkeypatch.setattr("app.services.agent_runtime.mode_runtime.experts_lead_decide", lambda *_a, **_k: {
        "action": "explain", "reply": "好的，我将为你分析。", "source": "llm",
        # Informational evidence answers do not require execution slots even if
        # the Lead classifier conservatively reports them missing.
        "confidence": 1.0, "user_job": "inform", "missing_slots": ["resume", "portrait"],
    })
    monkeypatch.setattr("app.utils.llm_client.get_llm_client", lambda: AnswerLLM())

    result = ModeRuntime().chat(
        "请只基于已确认简历中的前几项技能说明我的技术方向，不要创建计划或执行业务操作。",
        student_id="owner-1", interaction_mode="experts",
        prepared_turn={"visible_text": "请分析", "parts": [{"type": "text", "text": "请分析"}],
                       "turn_file_ids": [], "mount_errors": [], "attach_card": "",
                       "verified_task_refs": [], "verified_resource_refs": [], "resource_overviews": []},
    )

    assert "Python Web 后端方向" in result["reply"]
    assert "版本 resume-v9" in result["reply"]
    assert "下一页 offset=2" in result["reply"]
    assert "我将为你分析" not in result["reply"]
    assert result["tool_calls"] == []
    assert result["orchestration"]["supervisor_view"]["lead_action"] == "direct_answer"


def test_experts_evidence_answer_failure_is_partial_retry(monkeypatch):
    from app.services.agent_runtime.mode_runtime import ModeRuntime

    pack = {"status": "completed", "items": [{"data": "Python"}],
            "sources": [{"uri": "career://me/resume", "version": "v1"}],
            "coverage": {"complete": True, "returned": 1, "total": 1},
            "has_more": False, "next_offset": None, "unavailable": [], "continuations": []}
    class EmptyLLM:
        def chat(self, **_kwargs): return ""
    monkeypatch.setattr("app.services.db.get_mysql_client", lambda: object())
    monkeypatch.setattr("app.services.agent_runtime.evidence_retrieval.build_evidence_pack", lambda *_a, **_k: pack)
    monkeypatch.setattr("app.services.agent_runtime.mode_runtime.experts_lead_decide", lambda *_a, **_k: {
        "action": "explain", "reply": "好的，我将分析。", "user_job": "inform", "missing_slots": [],
    })
    monkeypatch.setattr("app.utils.llm_client.get_llm_client", lambda: EmptyLLM())

    result = ModeRuntime().chat(
        "说明我的技术方向", student_id="owner-1", interaction_mode="experts",
        prepared_turn={"visible_text": "说明我的技术方向", "parts": [], "turn_file_ids": [],
                       "mount_errors": [], "attach_card": "", "verified_task_refs": [],
                       "verified_resource_refs": [], "resource_overviews": []},
    )
    assert result["incomplete"] is True
    assert result["orchestration"]["strategy"] == "experts_evidence_retry"
    assert "请重试" in result["reply"]
from app.services.agent_runtime.plan import run_plan
from app.services.agent_runtime.plan_evidence import verify_plan_updates
from app.services.agent_runtime.quality_gate import turn_quality_gate
from app.services.agent_runtime.task_packet import build_standard_packet


def test_mentions_and_owner_link_do_not_fabricate_slots():
    assert _infer_known_slots("没有简历，不想做画像，岗位是空的", Blackboard()) == []
    assert _infer_known_slots("我的背景", Blackboard({"student.profile_ref": {"student_id": "owner"}})) == []


@pytest.mark.parametrize("reply", ["可以。", "Experts 模式适合多专家协作。", "我需要再确认一下目标", "不能生成该文件，因为缺少授权。"])
def test_valid_reply_not_rejected_by_words_or_length(reply):
    assert turn_quality_gate(reply, "请解释") == (True, "ok")


def test_invalid_model_template_never_rescued_by_keywords():
    result = _normalize({"action": "run_plan", "user_job": "execute", "template": "invalid", "confidence": 1},
                        "从简历到报告", plan_mode=True, hint=None, known_slots=["resume"], blackboard=Blackboard())
    assert result["action"] == "clarify" and result["template"] is None


class DB:
    def __init__(self, *, owner="owner", status="completed", tamper=False):
        self.portrait = {"source_portrait_task_id": "task_current",
                         "source_confirmation_task_id": "task_resume",
                         "source_self_report_revision": 0,
                         "summary": "无法这个词可以合法出现在字段里"}
        self.candidate = {"name": "测试同学", "skills": ["Python"]}
        self.profile = {**self.candidate, "_resume_confirmation": {
            "task_id": "task_resume", "student_id": "owner"}}
        self.resume_task = {"task_id": "task_resume", "student_id": "owner",
                            "pipeline": "L0_parse_resume", "status": "completed",
                            "result_json": {"confirmed": True, "candidate_profile": self.candidate}}
        self.task = {"task_id": "task_current", "student_id": owner, "status": status,
                     "pipeline": "L2_generate_portrait", "result_json": {"portrait_id": "8", "portrait": dict(self.portrait)}}
        if tamper:
            self.portrait["summary"] = "tampered"
        self.calls = []

    def fetch_all(self, sql, params):
        self.calls.append((sql, params))
        assert params.get("sid", "owner") == "owner"
        if "FROM school_tier_review" in sql:
            return []
        if "FROM student_profile" in sql:
            return [{"profile_json": self.profile}]
        if "FROM pipeline_task" in sql:
            if params.get("task_id") == "task_resume":
                return [self.resume_task]
            return [self.task]
        if "FROM student_portrait_4d" in sql:
            return [{"portrait_json": self.portrait}]
        raise AssertionError(sql)


def tool_out():
    return {"reply": "完成", "tool_calls": [{"tool_name": "pipeline_run", "artifact_candidates": [
        {"resource_type": "pipeline_task", "resource_id": "task_current"}]}],
        "blackboard_updates": {"portrait.current": {"portrait_id": "forged", "verified": True}}}


def test_only_current_tool_task_and_database_can_commit():
    db = DB()
    packet = build_standard_packet("profile", "生成画像")
    bb = Blackboard()
    results, _ = run_plan([packet], bb, lambda *a: tool_out(),
                          verify_updates=lambda p, out: verify_plan_updates(db, "owner", p, out))
    assert results[0].ok
    assert bb.get("portrait.current")["portrait_id"] == "8"
    assert db.calls


@pytest.mark.parametrize("fault", ["foreign_owner", "partial", "failed", "tamper", "no_tool", "old_board", "incomplete"])
def test_non_authoritative_or_old_result_never_completes(fault):
    db = DB(owner="victim" if fault == "foreign_owner" else "owner",
            status=fault if fault in {"partial", "failed"} else "completed", tamper=fault == "tamper")
    packet = build_standard_packet("profile", "生成画像")
    out = tool_out()
    bb = Blackboard()
    if fault in {"no_tool", "old_board"}:
        out["tool_calls"] = []
    if fault == "old_board":
        bb.write("portrait.current", {"resource_id": "old", "source_task_id": "old", "verified": True, "status": "completed"}, "profile")
    if fault == "incomplete":
        out["incomplete"] = True
    results, _ = run_plan([packet], bb, lambda *a: out,
                          verify_updates=lambda p, value: verify_plan_updates(db, "owner", p, value))
    assert not results[0].ok
    assert not results[0].blackboard_updates


def test_mode_runtime_and_turn_projection_stop_on_unverified_next_step(monkeypatch):
    import time
    from app.services.agent_runtime import mode_runtime as runtime
    from app.services.agent_runtime.egress_pipeline import project_turn
    from app.services.db import mysql_client
    db = DB()
    monkeypatch.setattr(mysql_client, "get_mysql_client", lambda: db)
    monkeypatch.setattr(runtime, "experts_lead_decide", lambda *a, **k: {
        "action": "run_plan", "template": "profile_match", "source": "llm", "handoff_brief": "test"})
    monkeypatch.setattr(runtime, "_invoke_subagent", lambda *a, **k: tool_out())
    board = Blackboard()
    result = runtime.ModeRuntime()._run_experts(
        "test", board, "", "", time.time(), "session", True, "", "owner")
    steps = result["orchestration"]["plan_results"]
    assert steps[0]["ok"] is True
    assert steps[1]["ok"] is False  # a profile task cannot complete the match step
    assert project_turn(result).to_dict()["outcome"]["status"] == "partial"


def test_ui_status_and_task_reference_ignore_natural_language():
    from app.services.agent_runtime.tool_result_channels import build_ui_view
    good = build_ui_view("pipeline_run", {"status": "completed", "task_id": "task_real",
                         "summary": "分析失败案例，不是 task_forged"})
    assert good["status"] == "done"
    assert good["parameters"]["task_id"] == "task_real"
    plain = build_ui_view("db_query", "失败案例记录 task_forged")
    assert plain["status"] == "partial"
    assert "task_id" not in plain["parameters"]
    assert build_ui_view("x", {"status": "failed", "summary": "一切成功"})["status"] == "error"
    assert build_ui_view("x", "成功", status="error")["status"] == "error"


def test_committed_ui_artifact_reference_also_requires_business_readback():
    out = tool_out()
    out["tool_calls"][0].pop("artifact_candidates")
    out["tool_calls"][0]["artifact_refs"] = [{"kind": "pipeline_task", "artifact_id": "artifact_stored",
                                               "preview": {"task_id": "task_current"}}]
    packet = build_standard_packet("profile", "生成画像")
    assert verify_plan_updates(DB(), "owner", packet, out)["portrait.current"]["portrait_id"] == "8"
    assert verify_plan_updates(DB(status="partial"), "owner", packet, out) == {}
