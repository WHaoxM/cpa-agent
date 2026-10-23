"""Canonical evidence retrieval is bounded and never trusts a model owner."""

import json
import pytest

from app.services.agent_runtime import evidence_retrieval as evidence


@pytest.mark.parametrize('portrait_source', ['confirm-v1', 'different-confirmation'])
@pytest.mark.parametrize('section_pin', [None,'chapter:experiences','no_reference','self_report_only'])
def test_resume_report_keeps_current_analysis_and_allows_same_source_portrait(monkeypatch, portrait_source, section_pin):
    uri = 'career://me/resume-reports/resume_' + 'a' * 32
    portrait_uri = 'career://me/portrait'
    calls = []
    monkeypatch.setattr(evidence, 'list_platform_resources', lambda *_a, **_k: {'resources': [
        {'uri': uri, 'version': 'confirm-v1', 'sections': ['overview', 'analysis', 'chapter:experiences']},
        {'uri': portrait_uri, 'version': 'portrait-v1', 'sections': ['overview', 'evidence']},
        {'uri': 'career://me/matches', 'sections': ['items']},
    ]})
    def read(_db, *, student_id, uri, section='overview', **kwargs):
        assert student_id == 'owner'
        calls.append((uri, section))
        return {'success': True, 'uri': uri, 'status': 'partial', 'business_artifact_verified': True,
            'analysis_content_status': 'current',
            'version': 'portrait-v1' if uri == portrait_uri else 'confirm-v1',
            'source_resume_version': portrait_source,
            'data': {'source_confirmation_task_id': 'confirm-v1'} if section == 'overview' else
                [{'kind': 'questions', 'text': '当时依据什么判断重复？'}] if section == 'analysis' else
                [{'text': '我逐项沟通分工。', 'provenance': {'kind': 'user_self_report', 'revision': 1}}],
            'coverage': {'total': 1, 'returned': 1, 'has_more': False, 'next_offset': None}}
    monkeypatch.setattr(evidence, 'read_platform_resource', read)
    def select(question, resources):
        if section_pin in {'no_reference','self_report_only'}:
            return {'needed':True,'answer_kind':'source_readback','reads':[
                {'uri':portrait_uri,'section':'evidence','dimension':'D3'},
                *([] if section_pin=='self_report_only' else [{'uri':uri,'section':'chapter:experiences'}])]}
        assert next(r for r in resources if r['uri']==uri)['reading_preview'][0]['text']=='当时依据什么判断重复？'
        assert {r['uri'] for r in resources} == ({uri, portrait_uri} if portrait_source == 'confirm-v1' else {uri})
        return [{'uri': portrait_uri, 'section': 'evidence', 'dimension': 'D3'}] if portrait_source == 'confirm-v1' else []
    monkeypatch.setattr(evidence, '_select_plan', select)
    pack = evidence.build_evidence_pack(object(), student_id='owner',
        question='我补充了沟通经历，请结合这份解读说明。',
        resource_refs=[] if section_pin in {'no_reference','self_report_only'} else [{'uri': uri, 'version': 'confirm-v1',**({'section':section_pin} if section_pin else {})}])
    assert (uri, 'analysis') in calls
    assert any(item['uri'] == portrait_uri for item in pack['items']) == (portrait_source == 'confirm-v1')
    assert any(item['uri'] == uri and item['section'] == 'analysis' for item in pack['items'])
    if portrait_source != 'confirm-v1':
        assert any(item['source'] == portrait_uri and item['status'] == 'source_version_mismatch' for item in pack['unavailable'])


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

    stale_report_uri = "career://me/reports/report_stale"
    stale_report = evidence.build_evidence_pack(
        object(), student_id="owner", question="继续问这份报告",
        resource_refs=[{"uri": stale_report_uri, "status": "reference_not_current"}],
    )
    assert stale_report["status"] == "unavailable" and stale_report["items"] == []
    assert stale_report["coverage"]["reason"] == "stale_report_reference"
    assert stale_report["unavailable"] == [{
        "source": stale_report_uri, "status": "reference_not_current",
    }]


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


def test_report_reference_adds_only_its_verified_current_resume_source(monkeypatch):
    report_uri = "career://me/reports/report_owner_1"
    report_version = "report-sha-1"
    resume_version = "confirm-v1"
    selected_catalog = []
    reads = []

    monkeypatch.setattr(evidence, "list_platform_resources", lambda _db, *, student_id: {
        "resources": [
            {"uri": "career://me/resume", "title": "已确认简历", "status": "completed",
             "version": resume_version, "sections": ["overview", "projects", "skills"]},
            {"uri": "career://me/matches", "title": "岗位匹配记录", "status": "available",
             "sections": ["overview", "items"]},
        ], "unavailable_sources": [],
    })

    def read(_db, *, student_id, uri, section, offset=0, limit=3, dimension=""):
        reads.append((student_id, uri, section))
        if uri == report_uri:
            return {"success": True, "status": "partial", "uri": uri,
                    "version": report_version, "business_artifact_verified": True,
                    "resource_id": "report_owner_1", "data": {
                        "sources": {"source_confirmation_task_id": resume_version},
                        "summary": "报告建议核对 Python 项目经验。",
                    }, "coverage": "overview_only"}
        if uri == "career://me/resume" and section == "projects":
            return {"success": True, "status": "completed", "uri": uri,
                    "version": resume_version, "data": [{"name": "课程数据整理", "role": "负责人"}],
                    "coverage": {"total": 1, "offset": 0, "returned": 1,
                                 "has_more": False, "next_offset": None}}
        raise AssertionError((uri, section))

    monkeypatch.setattr(evidence, "read_platform_resource", read)

    def select(question, resources):
        selected_catalog.extend(resources)
        return {"needed": True, "answer_kind": "analysis", "reads": [
            {"uri": report_uri, "section": "overview"},
            {"uri": "career://me/resume", "section": "projects"},
        ]}

    monkeypatch.setattr(evidence, "_select_plan", select)
    pack = evidence.build_evidence_pack(
        object(), student_id="owner", question="这份报告说我该先补什么，简历里有哪些相关项目？",
        resource_refs=[{"uri": report_uri, "version": report_version}],
    )

    assert {item["uri"] for item in selected_catalog} == {report_uri, "career://me/resume"}
    assert all(item["uri"] != "career://me/matches" for item in selected_catalog)
    assert all(owner == "owner" for owner, _, _ in reads)
    assert any(uri == "career://me/resume" and section == "projects" for _, uri, section in reads)
    assert any(item["uri"] == report_uri and item["version"] == report_version
               for item in pack["items"])
    assert any(item["uri"] == "career://me/resume" and item["version"] == resume_version
               for item in pack["items"])


def test_pinned_action_plan_forces_owner_read_of_student_submitted_practice_evidence(monkeypatch):
    plan_uri = "career://me/action-plans/plan_owned123"
    calls = []
    selected = []
    monkeypatch.setattr(evidence, "list_platform_resources", _catalog)

    def read(_mysql, *, student_id, uri, section, offset=0, limit=3, dimension=""):
        calls.append((student_id, uri, section))
        if uri == plan_uri and section == "overview":
            return {"success": True, "status": "draft", "uri": uri,
                    "version": "artifact_1:1:bodyhash", "data": {"title": "一周行动计划"},
                    "source_note": "计划来源当前有效。"}
        if uri == plan_uri and section == "evidence":
            return {"success": True, "status": "user_submitted", "uri": uri,
                    "version": "artifact_1:1:bodyhash",
                    "coverage": {"total": 1, "offset": offset, "returned": 1,
                                 "has_more": False, "next_offset": None},
                    "data": [{
                        "evidence_boundary": "学生提交的自述，未经核验。",
                        "step": {"title": "完成练习", "completed": True,
                                 "practice_record": {"source": "student_submitted",
                                                     "verified": False,
                                                     "summary": "我完成了合成样例。",
                                                     "attachment": {"read_status": "read",
                                                                    "content": "附件文本确实已读取。"}}},
                    }]}
        raise AssertionError((uri, section))

    monkeypatch.setattr(evidence, "read_platform_resource", read)
    monkeypatch.setattr(evidence, "_select_plan", lambda _q, resources: (
        selected.extend(resources) or {
            "needed": True, "answer_kind": "analysis",
            "reads": [{"id": "r1", "section": "overview"}],
        }
    ))
    pack = evidence.build_evidence_pack(
        object(), student_id="owner", question="复盘我的练习记录和岗位报告差距",
        resource_refs=[{"uri": plan_uri, "version": "artifact_1:1:bodyhash"}],
    )

    assert any(entry["uri"] == plan_uri and "evidence" in entry["sections"] for entry in selected)
    assert ("owner", plan_uri, "evidence") in calls
    assert any(item["uri"] == plan_uri and item["section"] == "evidence" for item in pack["items"])
    data = next(item["data"] for item in pack["items"] if item["section"] == "evidence")
    assert data["step"]["practice_record"]["verified"] is False
    assert data["step"]["practice_record"]["attachment"]["content"] == "附件文本确实已读取。"


def test_report_remains_usable_but_missing_resume_is_explicitly_partial(monkeypatch):
    report_uri = "career://me/reports/report_owner_2"
    resume_uri = "career://me/resume"
    report_version = "report-sha-2"
    resume_version = "confirm-v2"

    monkeypatch.setattr(evidence, "list_platform_resources", lambda _db, *, student_id: {
        "resources": [{"uri": resume_uri, "title": "已确认简历", "status": "completed",
                       "version": resume_version, "sections": ["overview", "projects"]}],
        "unavailable_sources": [],
    })

    def read(_db, *, student_id, uri, section, offset=0, limit=3, dimension=""):
        assert student_id == "owner"
        if uri == report_uri:
            return {"success": True, "status": "completed", "uri": uri,
                    "version": report_version, "business_artifact_verified": True,
                    "data": {"sources": {"source_confirmation_task_id": resume_version}},
                    "coverage": "overview_only"}
        if uri == resume_uri:
            raise LookupError("transient current resume read failure")
        raise AssertionError((uri, section))

    monkeypatch.setattr(evidence, "read_platform_resource", read)
    monkeypatch.setattr(evidence, "_select_plan", lambda _q, _resources: {
        "needed": True, "answer_kind": "analysis", "reads": [
            {"uri": report_uri, "section": "overview"},
            {"uri": resume_uri, "section": "projects"},
        ],
    })
    pack = evidence.build_evidence_pack(
        object(), student_id="owner", question="报告和简历项目有什么关系？",
        resource_refs=[{"uri": report_uri, "version": report_version}],
    )

    assert any(item["uri"] == report_uri for item in pack["items"])
    assert not any(item["uri"] == resume_uri for item in pack["items"])
    assert pack["status"] == "partial"
    assert any(source["uri"] == resume_uri and source["status"] == "unavailable"
               for source in pack["sources"])
    assert any(item["source"] == resume_uri and item["status"] == "unavailable"
               for item in pack["unavailable"])
    from app.services.agent_runtime.mode_runtime import (
        _apply_report_evidence_boundary, _evidence_summary_part,
    )
    summary = _evidence_summary_part(pack)
    assert summary and summary["scope_note"] == "resume_not_cross_checked"
    safe_reply = _apply_report_evidence_boundary("报告建议先核对技能要求。", summary)
    assert "不能与个人经历交叉核对" in safe_reply


def test_action_plan_evidence_summary_is_structured_and_marks_practice_unverified():
    from app.services.agent_runtime.mode_runtime import _evidence_summary_part

    summary = _evidence_summary_part({
        "status": "completed",
        "sources": [{
            "uri": "career://me/action-plans/plan_" + "a" * 20,
            "status": "user_submitted",
        }],
        "coverage": {"complete": True, "returned": 3, "total": 3},
    })
    assert summary == {
        "type": "evidence_summary", "title": "本轮参考资料", "status": "complete",
        "sources": [{"label": "一周行动计划", "status": "read"}],
        "scope_note": "student_submitted_unverified",
    }


def test_action_plan_evidence_summary_does_not_claim_unready_attachment_was_read():
    from app.services.agent_runtime.mode_runtime import _evidence_summary_part

    summary = _evidence_summary_part({
        "status": "completed",
        "sources": [{
            "uri": "career://me/action-plans/plan_" + "b" * 20,
            "status": "user_submitted",
        }],
        "items": [{
            "uri": "career://me/action-plans/plan_" + "b" * 20,
            "section": "evidence",
            "data": {"step": {"practice_record": {
                "attachment": {"read_status": "processing", "content": None},
            }}},
        }],
    })

    assert summary == {
        "type": "evidence_summary", "title": "本轮参考资料", "status": "partial",
        "sources": [
            {"label": "一周行动计划", "status": "read"},
            {"label": "练习附件", "status": "unavailable"},
        ],
        "scope_note": "student_submitted_unverified",
    }


def test_report_source_version_mismatch_fails_closed_before_report_content(monkeypatch):
    report_uri = "career://me/reports/report_owner_3"
    calls = []

    monkeypatch.setattr(evidence, "list_platform_resources", lambda _db, *, student_id: {
        "resources": [{"uri": "career://me/resume", "title": "已确认简历", "status": "completed",
                       "version": "confirm-current", "sections": ["overview", "projects"]}],
        "unavailable_sources": [],
    })

    def read(_db, **kwargs):
        calls.append(kwargs)
        return {"success": True, "status": "completed", "uri": report_uri,
                "version": "report-v1", "business_artifact_verified": True,
                "data": {"sources": {"source_confirmation_task_id": "confirm-old"}}}

    monkeypatch.setattr(evidence, "read_platform_resource", read)
    monkeypatch.setattr(evidence, "_select_plan", lambda *_: (_ for _ in ()).throw(
        AssertionError("a mixed-version report must not reach selection")))
    pack = evidence.build_evidence_pack(
        object(), student_id="owner", question="报告里说了什么？",
        resource_refs=[{"uri": report_uri, "version": "report-v1"}],
    )
    assert len(calls) == 1
    assert pack["items"] == [] and pack["status"] == "unavailable"
    assert pack["coverage"]["reason"] == "source_version_mismatch"


def test_exploration_focus_and_practice_precede_optional_long_assignment(monkeypatch):
    uri = 'career://me/explorations/exp_'+'a'*24+'/rounds/round_'+'b'*24
    plan = 'career://me/action-plans/plan_'+'c'*20
    monkeypatch.setattr(evidence, 'list_platform_resources', lambda *a, **k: {'resources': [], 'unavailable_sources': []})
    def read(_db, *, student_id, uri, section='overview', **kwargs):
        assert student_id == 'owner'
        data = ({'plan_uri': plan, 'question': '联表困难', 'source_status': 'current'} if section == 'overview'
                else [{'source': 'confirmed_resume', 'skills': ['SQL']}] if section == 'facts'
                else [{'step': {'practice_record': {'summary': '联表仍有重复行'}}}] if section == 'evidence'
                else '未执行的计划要求。'*1000)
        return {'success': True, 'status': 'user_submitted', 'uri': uri, 'version': 'current',
                'section': section, 'data': data, 'coverage': {'total': 1, 'returned': 1, 'complete': True, 'has_more': False}}
    monkeypatch.setattr(evidence, 'read_platform_resource', read)
    monkeypatch.setattr(evidence, '_select_plan', lambda *_: [{'uri': plan, 'section': 'body'}])
    pack = evidence.build_evidence_pack(object(), student_id='owner', question='请复盘这次尝试', resource_refs=[uri], max_chars=4000)
    assert pack['items'][0]['uri'] == uri and pack['items'][0]['section'] == 'overview'
    assert '联表仍有重复行' in json.dumps(pack['items'], ensure_ascii=False)
    assert pack['status'] == 'partial' and pack['has_more'] is True
