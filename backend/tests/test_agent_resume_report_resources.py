"""Current confirmed resume report can be read by the main Agent, not browser claims."""

from copy import deepcopy

import pytest

from app.services.agent_runtime import resource_catalog, resume_report_resources as resources
from app.services import resume_diagnostic_report as service
from app.services import job_catalog_listing


RID = "resume_" + "a" * 32
URI = f"{resources.RESUME_REPORTS_URI}/{RID}"
REPORT = {
    "report_id": RID, "report_type": "resume_diagnostic", "status": "partial",
    "source_confirmation_task_id": "task_current", "coverage": {"page_count": 2},
    "missing_sections": ["unparsed_source_pages"],
    "limitations": ["没有岗位来源，不给出匹配分"],
    "quality_status": "model_interpretation_unverified",
    "analysis": {"summary": {"text": "需核对的模型综述", "evidence_ids": ["projects_0"]},
                 "strengths": [{"text": "项目经验", "evidence_ids": ["projects_0"]}],
                 "questions": []},
    "chapters": [
        {"id": "projects", "title": "项目", "items": [
            {"label": f"项目{i}", "evidence_refs": [{"page": i + 1}]} for i in range(5)
        ]},
        {"id": "skills", "title": "技能", "items": [{"label": "Python", "evidence_refs": [{"page": 1}]}]},
    ],
}


def test_resume_reading_uses_existing_single_repair_without_rewriting_or_rerolling():
    import json
    from app.services.agent_runtime.mode_runtime import _ask_answer_or_repair
    pack={'items': [{'uri': URI, 'section': 'analysis', 'data': {'kind': 'unclear_expression'}}]}
    bad='这能看出你有数据聚合意识和落地能力。比如按身份证号和手机号判重？'
    good='原文写明了用SQL统计各活动报名人数。核对重复报名的判断依据还没写明，补清后读者才能看懂结果怎样得出。'
    class Client:
        calls=[]
        def chat(self, **kwargs):
            self.calls.append(kwargs)
            return json.dumps({'reply': good}, ensure_ascii=False)
    client=Client()
    messages=[{'role':'system','content':'只读当前资料'},{'role':'user','content':'已有原文及当前来源问题'}]
    assert _ask_answer_or_repair(client,bad,messages,temperature=.4,max_tokens=1000,evidence_pack=pack)==([],good)
    assert len(client.calls)==1 and client.calls[0]['messages'][1]==messages[1]
    assert client.calls[0]['response_format']['type']=='json_schema'
    # A second bad draft is not retried or published as a successful answer.
    client.calls=[]
    client.chat=lambda **kwargs: (client.calls.append(kwargs) or json.dumps({'reply': bad},ensure_ascii=False))
    assert _ask_answer_or_repair(client,bad,messages,temperature=.4,max_tokens=1000,evidence_pack=pack)==([],'')
    assert len(client.calls)==1


def test_resume_reading_boundary_is_not_a_global_ask_filter():
    from app.services.agent_runtime.resume_report_resources import resume_reading_answer_error
    assert resume_reading_answer_error('比如用SQL说明原理。', {'items': []}) is None


def test_source_readback_projects_current_actual_data_and_never_calls_answer_model(monkeypatch):
    import time
    from app.services.agent_runtime.mode_runtime import ModeRuntime
    from app.services.agent_runtime.blackboard import Blackboard
    from app.services.agent_runtime.resume_report_resources import source_readback_answer
    pack={'answer_kind':'source_readback','status':'completed','coverage':{'complete':True},
        'items':[{'uri':URI,'section':'analysis','version':'confirm1','analysis_content_status':'current',
                  'data':{'kind':'summary','text':'这一份报告实际解释了按活动统计报名人数。'}},
                 {'uri':URI,'section':'analysis','version':'confirm1','analysis_content_status':'current',
                  'data':{'kind':'unclear_expression','text':'当时依据什么判断重复？','why':'补清后能看懂结果怎样得出。'}}]}
    monkeypatch.setattr('app.utils.llm_client.get_llm_client', lambda: (_ for _ in ()).throw(AssertionError('Do not regenerate a saved reading')))
    reply=ModeRuntime()._run_ask(message='读回这份解读',blackboard=Blackboard(),prefix='',hist=[],
        start=time.time(),session_id=None,upgrade_hint=None,memory_mount='',evidence_pack=pack)['reply']
    assert '这一份报告实际解释了按活动统计报名人数。' in reply and '当时依据什么判断重复？' in reply
    more=deepcopy(pack)
    more['items'].append({**pack['items'][0],'data':{'kind':'strengths','text':'另一项经历有自己的独有解读，仍须完整保留。'}})
    assert '另一项经历有自己的独有解读，仍须完整保留。' in source_readback_answer(more)
    assert source_readback_answer({**pack,'budget_exceeded':True}) is None
    other=deepcopy(pack);other['items'][0]['data']['text']='另一份当前报告中的不同解读。'
    assert '另一份当前报告中的不同解读。' in source_readback_answer(other)
    assert '这一份报告实际解释了' not in source_readback_answer(other)
    legacy=deepcopy(pack)
    for item in legacy['items']:item['analysis_content_status']='legacy'
    assert source_readback_answer(legacy) is None
    assert source_readback_answer({**pack,'answer_kind':'analysis'}) is None
    supplemented=deepcopy(pack)
    supplemented['items'].append({'uri':'career://me/portrait','dimension':'D3',
        'data':{'trait':'communication','text':'我整理并沟通了同学的问题。','provenance':{'kind':'user_self_report','revision':1}}})
    answer=source_readback_answer(supplemented)
    assert '本人自述' in answer and '我整理并沟通了同学的问题。' in answer
    assert '当时依据什么判断重复？' not in answer
    assert '独立核验' in answer


def test_reading_evidence_labels_survive_public_and_history_normalization():
    from app.services.agent_runtime.turn_contract import normalize_message_parts
    part={'type':'evidence_summary','status':'complete','sources':[
        {'label':'简历解读','status':'read'}, {'label':'沟通与协作补充','status':'read'}]}
    assert normalize_message_parts([part])[0]['sources']==part['sources']


def test_catalog_preview_uses_the_actual_current_reading_not_only_a_title(monkeypatch):
    monkeypatch.setattr(resources,'_current',lambda mysql,student_id:{**deepcopy(REPORT),'analysis_content_status':'current'})
    found=resources.list_resume_report_resources(object(),student_id='owner')['resources'][-1]
    assert found['reading_preview'][0]['text']==REPORT['analysis']['summary']['text']


def test_missing_readback_never_falls_through_to_new_model_advice(monkeypatch):
    import time
    from app.services.agent_runtime.mode_runtime import ModeRuntime
    from app.services.agent_runtime.blackboard import Blackboard
    monkeypatch.setattr('app.utils.llm_client.get_llm_client',lambda:(_ for _ in ()).throw(AssertionError('No new inference for a failed readback')))
    result=ModeRuntime()._run_ask(message='读回原结果',blackboard=Blackboard(),prefix='',hist=[],
        start=time.time(),session_id=None,upgrade_hint=None,memory_mount='',evidence_pack={'answer_kind':'source_readback','items':[]})
    from app.services.agent_runtime.turn_contract import derive_turn_outcome
    assert '还没有读到' in result['reply'] and derive_turn_outcome(result)['status']=='partial'


def test_list_and_page_current_resume_report(monkeypatch):
    seen = []

    def read(mysql, *, student_id, report_id=None):
        seen.append((student_id, report_id))
        if student_id != "owner" or report_id not in (None, RID):
            raise LookupError("not owner")
        return deepcopy(REPORT)

    monkeypatch.setattr(service, "read_resume_diagnostic", read)
    catalog = resources.list_resume_report_resources(object(), student_id="owner")
    assert [r["uri"] for r in catalog["resources"]] == [resources.RESUME_REPORTS_URI, URI]
    assert catalog["resources"][1]["business_artifact_verified"] is True
    assert {item["section"]: item["title"] for item in catalog["resources"][1]["section_details"]}[
        "chapter:projects"] == "项目"
    overview = resource_catalog.read_platform_resource(object(), student_id="owner", uri=URI)
    assert overview["data"]["chapter_count"] == 2
    assert "项目0" not in str(overview)
    first = resource_catalog.read_platform_resource(
        object(), student_id="owner", uri=URI, section="chapter:projects", limit=3,
    )
    second = resource_catalog.read_platform_resource(
        object(), student_id="owner", uri=URI, section="chapter:projects", offset=3,
    )
    assert first["coverage"]["has_more"] is True
    assert first["coverage"]["next_offset"] == 3
    assert second["coverage"]["complete"] is True
    assert len(first["data"] + second["data"]) == 5
    analysis = resource_catalog.read_platform_resource(object(), student_id="owner", uri=URI,
                                                       section="analysis", limit=1)
    assert analysis["data"][0]["text"] == "需核对的模型综述"
    assert analysis["quality_status"] == "model_interpretation_unverified"
    assert analysis["coverage"]["has_more"] is True
    assert all(sid == "owner" for sid, _ in seen)


def test_other_owner_stale_or_missing_report_not_exposed(monkeypatch):
    def read(mysql, *, student_id, report_id=None):
        if student_id != "owner":
            raise LookupError("not owner")
        return deepcopy(REPORT)

    monkeypatch.setattr(service, "read_resume_diagnostic", read)
    with pytest.raises(ValueError):
        resource_catalog.read_platform_resource(object(), student_id="owner", uri=URI, section="chapter:unknown")
    monkeypatch.setattr(service, "read_resume_diagnostic", lambda mysql, *, student_id, report_id=None: (
        (_ for _ in ()).throw(LookupError("stale or missing"))
    ))
    catalog = resources.list_resume_report_resources(object(), student_id="owner")
    assert catalog["resources"][0]["status"] == "empty"
    with pytest.raises(LookupError):
        resource_catalog.read_platform_resource(object(), student_id="other", uri=URI)


def test_job_catalog_resource_is_live_and_owner_scoped(monkeypatch):
    def read(mysql, *, student_id, report_id=None):
        if student_id != "owner" or report_id not in (None, RID):
            raise LookupError("not owner")
        return deepcopy(REPORT)

    revision = {"value": 1}

    def list_jobs(mysql, *, limit, offset):
        assert (limit, offset) == (2, 0)
        return {"status": "ready", "total": 1, "items": [{
            "job_portrait_id": "job_1", "publication_version": revision["value"],
            "position_name": "AI 产品实习生", "vacancy_validity": "unknown",
        }], "catalog_version": f"catalog_{revision['value']}",
                "catalog_latest_published_at": "2026-10-10T01:00:00", "has_more": False}

    monkeypatch.setattr(service, "read_resume_diagnostic", read)
    monkeypatch.setattr(job_catalog_listing, "list_published_job_candidates", list_jobs)
    first = resource_catalog.read_platform_resource(
        object(), student_id="owner", uri=URI, section="job_catalog", limit=2,
    )
    revision["value"] = 2
    second = resource_catalog.read_platform_resource(
        object(), student_id="owner", uri=URI, section="job_catalog", limit=2,
    )
    assert first["version"] == "catalog_1"
    assert second["version"] == "catalog_2"
    assert second["data"][0]["publication_version"] == 2
    assert second["source_confirmation_task_id"] == "task_current"
    assert second["coverage"]["complete"] is True
    with pytest.raises(LookupError):
        resource_catalog.read_platform_resource(
            object(), student_id="other", uri=URI, section="job_catalog", limit=2,
        )
