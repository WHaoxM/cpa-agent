"""Canonical evidence retrieval is bounded and never trusts a model owner."""

import json

from app.services.agent_runtime import evidence_retrieval as evidence


def _catalog(mysql, *, student_id):
    assert student_id == "owner"
    return {"resources": [{
        "uri": "career://me/resume", "title": "已确认简历", "status": "completed",
        "version": "confirm-v1", "sections": ["overview", "skills"],
    }], "unavailable_sources": []}


def _skills(mysql, *, student_id, uri, section, offset, limit, dimension):
    assert student_id == "owner"
    assert uri == "career://me/resume"
    assert section == "skills" and dimension == ""
    values = [f"技能{i + 1}" for i in range(35)]
    data = values[offset:offset + limit]
    more = offset + len(data) < len(values)
    return {"success": True, "status": "completed", "uri": uri, "section": section,
            "version": "confirm-v1", "data": data,
            "coverage": {"total": 35, "offset": offset, "returned": len(data),
                         "has_more": more, "next_offset": offset + len(data) if more else None}}


def test_questions_get_ten_of_thirty_five_with_exact_continuation(monkeypatch):
    monkeypatch.setattr(evidence, "list_platform_resources", _catalog)
    monkeypatch.setattr(evidence, "read_platform_resource", _skills)
    monkeypatch.setattr(evidence, "_select_plan", lambda question, resources: [
        {"uri": "career://me/resume", "section": "skills"}])
    pack = evidence.build_evidence_pack(object(), student_id="owner", question="列出我的前十项技能")
    assert pack["status"] == "partial"
    assert pack["coverage"] == {"complete": False, "returned": 10, "total": 35,
                                "reason": "item_limit"}
    assert pack["next_offset"] == 10 and pack["has_more"] is True
    assert [item["data"] for item in pack["items"]] == [f"技能{i + 1}" for i in range(10)]
    assert all(item["version"] == "confirm-v1" and item["uri"] == "career://me/resume"
               for item in pack["items"])

    cursor = pack["continuations"][0]
    assert cursor == {"uri": "career://me/resume", "section": "skills",
                      "dimension": "", "next_offset": 10, "version": "confirm-v1"}
    second = evidence.build_evidence_pack(object(), student_id="owner", question="继续",
                                          continuation=cursor)
    assert [item["data"] for item in second["items"]] == [f"技能{i + 1}" for i in range(10, 20)]
    assert second["coverage"]["total"] == 35
    assert second["next_offset"] == 20


def test_continuation_rechecks_owner_and_version(monkeypatch):
    monkeypatch.setattr(evidence, "list_platform_resources", _catalog)
    monkeypatch.setattr(evidence, "_select_plan", lambda *_: (_ for _ in ()).throw(
        AssertionError("continuation must not select a new first page")))
    cursor = {"uri": "career://me/resume", "section": "skills", "dimension": "",
              "next_offset": 10, "version": "confirm-v1"}
    calls = []

    def changed(mysql, **kwargs):
        calls.append(kwargs)
        result = _skills(mysql, **kwargs)
        result["version"] = "confirm-v2"
        return result

    monkeypatch.setattr(evidence, "read_platform_resource", changed)
    stale = evidence.build_evidence_pack(object(), student_id="owner", question="继续",
                                         continuation=cursor)
    assert calls[0]["offset"] == 10 and calls[0]["student_id"] == "owner"
    assert stale["items"] == [] and stale["coverage"]["reason"] == "source_unavailable"
    assert stale["unavailable"][0]["status"] == "version_changed"


def test_char_budget_never_slices_an_item(monkeypatch):
    monkeypatch.setattr(evidence, "list_platform_resources", _catalog)
    monkeypatch.setattr(evidence, "read_platform_resource", _skills)
    monkeypatch.setattr(evidence, "_select_plan", lambda q, r: [
        {"uri": "career://me/resume", "section": "skills"}])
    pack = evidence.build_evidence_pack(object(), student_id="owner", question="技能",
                                        max_chars=10)
    assert pack["items"] == []
    assert pack["status"] == "unavailable"
    assert pack["coverage"]["reason"] == "budget_too_small"
    assert pack["budget_exceeded"] is True
    assert pack["has_more"] is True and pack["next_offset"] == 0

    bounded = evidence.build_evidence_pack(object(), student_id="owner", question="技能",
                                           max_chars=600)
    assert bounded["budget_exceeded"] is False
    assert bounded["serialized_chars"] <= 600
    assert bounded["has_more"] is True


def test_model_plan_cannot_select_outside_catalog_or_owner(monkeypatch):
    monkeypatch.setattr(evidence, "list_platform_resources", _catalog)
    calls = []
    monkeypatch.setattr(evidence, "read_platform_resource", lambda *a, **kw: calls.append(kw))
    monkeypatch.setattr(evidence, "_select_plan", lambda q, r: [
        {"uri": "career://me/action-plans/plan_other", "section": "overview"},
        {"uri": "career://me/resume", "section": "invented"}])
    pack = evidence.build_evidence_pack(object(), student_id="owner", question="别人的计划")
    assert pack["status"] == "unavailable" and pack["items"] == []
    assert calls == []
    assert pack["coverage"]["reason"] == "no_valid_selection"


def test_explicit_ref_is_rechecked_under_owner_and_never_leaks_other_user(monkeypatch):
    monkeypatch.setattr(evidence, "list_platform_resources", _catalog)
    calls = []

    def deny(mysql, **kwargs):
        calls.append(kwargs)
        raise LookupError("other owner")

    monkeypatch.setattr(evidence, "read_platform_resource", deny)
    pack = evidence.build_evidence_pack(
        object(), student_id="owner", question="读这份资料",
        resource_refs=[{"uri": "career://me/action-plans/plan_other"}],
    )
    assert calls[0]["student_id"] == "owner"
    assert pack["items"] == [] and pack["status"] == "unavailable"
    assert pack["unavailable"] == [{"source": "career://me/action-plans/plan_other",
                                     "section": "overview", "status": "unavailable"}]


def test_explicit_collection_ref_selects_relevant_section_not_forced_overview(monkeypatch):
    monkeypatch.setattr(evidence, "list_platform_resources", _catalog)
    monkeypatch.setattr(evidence, "read_platform_resource", _skills)
    monkeypatch.setattr(evidence, "_select_plan", lambda q, r: [
        {"uri": "career://me/resume", "section": "skills"}])
    pack = evidence.build_evidence_pack(object(), student_id="owner", question="列出技能",
                                        resource_refs=[{"uri": "career://me/resume"}])
    assert len(pack["items"]) == 10
    assert pack["sources"][0]["section"] == "skills"


def test_explicit_ref_version_is_carried_through_section_planning_and_read(monkeypatch):
    monkeypatch.setattr(evidence, "list_platform_resources", _catalog)
    monkeypatch.setattr(evidence, "read_platform_resource", _skills)
    monkeypatch.setattr(evidence, "_select_plan", lambda q, r: [
        {"uri": "career://me/resume", "section": "skills"}])
    current = evidence.build_evidence_pack(
        object(), student_id="owner", question="列出技能",
        resource_refs=[{"uri": "career://me/resume", "version": "confirm-v1"}],
    )
    assert current["sources"][0]["version"] == "confirm-v1"
    assert current["coverage"] == {"complete": False, "returned": 10,
                                   "total": 35, "reason": "item_limit"}

    stale = evidence.build_evidence_pack(
        object(), student_id="owner", question="列出技能",
        resource_refs=[{"uri": "career://me/resume", "version": "old-confirmation"}],
    )
    assert stale["items"] == []
    assert stale["unavailable"] == [{
        "source": "career://me/resume", "section": "skills", "status": "version_changed"}]
    assert stale["coverage"]["reason"] == "source_unavailable"


def test_status_only_or_mixed_task_refs_fail_closed_without_planning_current_resources(monkeypatch):
    monkeypatch.setattr(evidence, "list_platform_resources", _catalog)
    monkeypatch.setattr(evidence, "read_platform_resource", lambda *a, **kw: (_ for _ in ()).throw(
        AssertionError("status-only task refs must not read a current resource")))
    monkeypatch.setattr(evidence, "_select_plan", lambda *_: (_ for _ in ()).throw(
        AssertionError("status-only task refs must not invoke planner")))
    unbound = {"task_id": "task_old", "status": "reference_not_current", "task_status": "partial"}
    for refs in ([unbound], [{"uri": "career://me/resume", "version": "confirm-v1"}, unbound]):
        pack = evidence.build_evidence_pack(object(), student_id="owner", question="读项目", resource_refs=refs)
        assert pack["items"] == []
        assert pack["status"] == "unavailable"
        assert pack["coverage"]["complete"] is False
        assert pack["coverage"]["reason"] == "reference_not_current"
        assert pack["unavailable"] == [{
            "source": "task_reference", "task_id": "task_old", "status": "reference_not_current"}]


def test_selection_failure_and_unrelated_chat_do_not_fabricate_evidence(monkeypatch):
    monkeypatch.setattr(evidence, "list_platform_resources", _catalog)
    monkeypatch.setattr(evidence, "read_platform_resource", lambda *a, **kw: (_ for _ in ()).throw(
        AssertionError("must not read")))
    monkeypatch.setattr(evidence, "_select_plan", lambda q, r: None)
    failed = evidence.build_evidence_pack(object(), student_id="owner", question="技能")
    assert failed["coverage"]["reason"] == "selection_unavailable"
    assert failed["unavailable"] == [{"source": "evidence_selection", "status": "unavailable"}]
    monkeypatch.setattr(evidence, "_select_plan", lambda q, r: {"needed": False, "reads": []})
    irrelevant = evidence.build_evidence_pack(object(), student_id="owner", question="你好")
    assert irrelevant["items"] == [] and irrelevant["unavailable"] == []
    assert irrelevant["status"] == "completed"
    assert irrelevant["coverage"]["reason"] == "evidence_not_needed"


def test_short_id_selection_maps_only_server_discovered_uri(monkeypatch):
    from app.utils import llm_client

    class FakeLLM:
        def __init__(self, payload):
            self.payload = payload

        def chat(self, **kwargs):
            catalog = json.loads(kwargs["messages"][1]["content"])["catalog"]
            assert catalog == [{"id": "r0", "title": "已确认简历",
                                "status": "completed", "sections": ["overview", "skills"]}]
            return json.dumps(self.payload)

    resources = _catalog(None, student_id="owner")["resources"]
    monkeypatch.setattr(llm_client, "get_llm_client", lambda: FakeLLM({
        "needed": True, "reads": [{"id": "r0", "section": "skills"}]}))
    assert evidence._select_plan("列出技能", resources) == {
        "needed": True, "answer_kind": "analysis", "reads": [{"uri": "career://me/resume", "section": "skills", "dimension": ""}]}
    monkeypatch.setattr(llm_client, "get_llm_client", lambda: FakeLLM({
        "needed": True, "reads": [{"id": "r999", "section": "skills"}]}))
    proposed = evidence._select_plan("读别人的资料", resources)
    assert evidence._validated_plan(proposed["reads"], resources) == []
