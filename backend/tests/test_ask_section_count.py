import json

import pytest

from app.services.agent_runtime import evidence_retrieval as retrieval
from app.services.agent_runtime.quality_gate import section_count_answer


def _pack(monkeypatch):
    uri = "career://me/resume-reports/current"
    monkeypatch.setattr(retrieval, "list_platform_resources", lambda *a, **kw: {"resources": [{
        "uri": uri, "status": "completed", "sections": ["chapter:experience"],
        "section_details": [{"section": "chapter:experience", "title": "项目与实践"}]}]})
    monkeypatch.setattr(retrieval, "_select_plan", lambda *a: {
        "needed": True, "answer_kind": "section_count", "reads": [{"uri": uri, "section": "chapter:experience"}]})

    def read(*a, **kw):
        offset = kw["offset"]
        return {"success": True, "status": "completed", "version": "confirm1", "data": list(range(offset, offset+3)),
                "coverage": {"total": 6, "has_more": offset == 0, "next_offset": 3 if offset == 0 else None}}

    monkeypatch.setattr(retrieval, "read_platform_resource", read)
    return retrieval.build_evidence_pack(object(), student_id="owner", question="章节有几项？")


def test_server_count_uses_authoritative_chapter_and_complete_pages(monkeypatch):
    pack = _pack(monkeypatch)
    reply = section_count_answer(pack)
    assert "**6 条记录**" in reply and "项目与实践" in reply and "confirm1" in reply
    assert "不证明原始 PDF" in reply and "不能单凭条数" in reply


@pytest.mark.parametrize("mutation", [
    {"answer_kind": "analysis"}, {"count_scope": {}}, {"sources": []},
    {"has_more": True}, {"truncated": True}, {"budget_exceeded": True},
    {"unavailable": [{"status": "version_changed"}]},
    {"coverage": {"complete": False, "total": 6, "returned": 3}},
    {"coverage": {"complete": True, "total": True, "returned": 1}},
])
def test_noncount_or_unreliable_scope_never_shortcuts(monkeypatch, mutation):
    pack = _pack(monkeypatch)
    pack.update(mutation)
    assert section_count_answer(pack) is None


def test_count_no_answer_model_call(monkeypatch):
    from app.services.agent_runtime.mode_runtime import ModeRuntime
    from app.services.agent_runtime.blackboard import Blackboard
    import time
    pack = _pack(monkeypatch)
    monkeypatch.setattr("app.utils.llm_client.get_llm_client", lambda: (_ for _ in ()).throw(AssertionError("extra call")))
    result = ModeRuntime()._run_ask(message="count", blackboard=Blackboard(), prefix="", hist=[],
        start=time.time(), session_id=None, upgrade_hint=None, memory_mount="", evidence_pack=pack)
    assert "6 条记录" in result["reply"]
    assert result["tool_calls"] == []


def test_selector_missing_unknown_and_truncated_intent_defaults_to_analysis(monkeypatch):
    resources = [{"uri": "career://me/r", "sections": ["chapter:x"]}]
    payload = {"needed": True, "reads": [{"id": "r0", "section": "chapter:x"}]}

    class Client:
        def chat(self, **kw):
            return json.dumps(payload)

    monkeypatch.setattr("app.utils.llm_client.get_llm_client", lambda: Client())
    assert retrieval._select_plan("q", resources)["answer_kind"] == "analysis"
    payload["answer_kind"] = "unknown"
    assert retrieval._select_plan("q", resources)["answer_kind"] == "analysis"
    payload["answer_kind"] = "section_count"
    assert retrieval._select_plan("q", resources)["answer_kind"] == "section_count"
    assert retrieval._select_plan("q" * 2001, resources)["answer_kind"] == "analysis"
