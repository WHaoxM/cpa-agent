import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import pytest

from app.services.resume_diagnostic_report import (
    build_resume_diagnostic_report, _synthesize, _synthesize_role_directions,
    read_resume_diagnostic)
from app.services.job_publication import canonical_json, content_sha256


@pytest.fixture
def captured_scope_failure():
    return json.loads((Path(__file__).parent/'fixtures/resume_diagnostic_scope_failure.json').read_text(encoding='utf8'))


def test_captured_real_diagnostic_cannot_attribute_the_whole_project_to_student(captured_scope_failure):
    case=captured_scope_failure
    class CapturedClient:
        def chat_with_metadata(self, **kwargs):
            return SimpleNamespace(text=json.dumps(case['analysis'],ensure_ascii=False),finish_reason='stop',
                context_status=SimpleNamespace(fits=True),usage={},model='captured-real-qwen-plus')
    result=_synthesize(build_resume_diagnostic_report(case['profile'],student_id='scope-test'),llm_client=CapturedClient())
    assert result['analysis'] is None
    assert result['analysis_status'] in {'invalid_grounded_interpretation','model_unavailable_or_invalid'}


def _reading_candidate(facts):
    fact=next(f for f in facts if f['id']=='projects_0')
    def point(text,needle):
        fragment=next(f for f in fact['fragments'] if needle in f['text'])
        return {'text':text,'evidence_ids':[fact['id']],
                'anchors':[{'evidence_id':fact['id'],'fragment_id':fragment['id']}]}
    option=next(q for q in fact['question_choices'] if q['focus']=='contribution' and
                any(f['id']==q['fragment_id'] and 'Vue' in f['text'] for f in fact['fragments']))
    question={'choice':option['key']}
    return {'summary':point('报名统计的用途是比较各活动的报名情况；说明重复记录怎样计数，能让读者看懂这些人数的含义。','SQL'),
            'strengths':[],'questions':[question]}


def test_report_guard_does_not_borrow_attendance_from_other_experiences(captured_scope_failure):
    from app.services.resume_diagnostic_report import synthesis_facts,validate_synthesis
    profile=deepcopy(captured_scope_failure['profile'])
    profile['projects'].append({'name':'另一项签到核对','desc':'我核对签到和实际到场人数。'})
    facts=synthesis_facts(build_resume_diagnostic_report(profile,student_id='scope-test'))
    value=_reading_candidate(facts)
    value['summary']['text']='报名统计能说明实际到场人数。'
    with pytest.raises(ValueError,match='报名不等于'):validate_synthesis(value,facts)


def test_one_model_draft_provides_reading_and_the_same_useful_question(captured_scope_failure):
    from app.services.resume_diagnostic_report import synthesis_facts
    from app.services.resume_improvements import synthesize
    report=build_resume_diagnostic_report(captured_scope_failure['profile'],student_id='scope-test')
    candidate=_reading_candidate(synthesis_facts(report))
    calls=[]
    class Client:
        def chat_with_metadata(self,**kw):
            calls.append(kw)
            return SimpleNamespace(text=json.dumps(candidate,ensure_ascii=False),finish_reason='stop',
                context_status=SimpleNamespace(fits=True),usage={},model='deterministic')
    result=_synthesize(report,llm_client=Client())
    assert result['analysis'] is not None
    assert result['improvements_status']=='ready'
    assert result['analysis']['questions'][0]['text']==result['improvements'][0]['questions'][0]
    assert result['improvements'][0]['why']==result['analysis']['questions'][0]['why']
    assert '用 Vue 展示查询结果' in result['improvements'][0]['questions'][0]
    assert '没有参与' in result['improvements'][0]['questions'][0]
    assert '接口' not in result['improvements'][0]['questions'][0]
    assert len(calls)==1
    assert result['analysis']['summary']['text']==candidate['summary']['text']
    assert result['analysis']['summary']['source_quotes'][0]['text']=='我编写 SQL 分组查询统计各活动报名人数'
    assert result['analysis_version']==20


def test_source_is_separate_from_interpretation_not_repeated_in_every_paragraph(captured_scope_failure):
    from app.services.resume_diagnostic_report import synthesis_facts, validate_synthesis
    facts=synthesis_facts(build_resume_diagnostic_report(captured_scope_failure['profile'],student_id='scope-test'))
    candidate=_reading_candidate(facts);candidate['questions']=[]
    result=validate_synthesis(candidate,facts)
    assert result['summary']['text']==candidate['summary']['text']
    assert result['summary']['source_quotes'][0]['text'] not in result['summary']['text']


def test_actual_generation_contract_asks_for_reading_not_personality_strengths(captured_scope_failure):
    from app.services.resume_diagnostic_report import synthesis_facts
    report=build_resume_diagnostic_report(captured_scope_failure['profile'],student_id='scope-test')
    candidate=_reading_candidate(synthesis_facts(report))
    class Client:
        def chat_with_metadata(self,**kwargs):
            schema=kwargs['response_format']['json_schema']['schema']
            assert 'reading_notes' in schema['properties']
            assert 'strengths' not in schema['properties']
            assert 'what_is_clear' in schema['properties'] and 'summary' not in schema['properties']
            prompt=kwargs['messages'][0]['content']
            assert '任务的产物' in prompt
            assert '不重复原文、项目名或本人动作' not in prompt
            return SimpleNamespace(text=json.dumps({**{k:v for k,v in candidate.items() if k!='strengths'},
                'reading_notes':[]},ensure_ascii=False),finish_reason='stop',
                context_status=SimpleNamespace(fits=True),usage={},model='deterministic')
    result=_synthesize(report,llm_client=Client())
    assert result['analysis'] is not None
    assert result['analysis']['summary']['text']==candidate['summary']['text']
    assert result['analysis']['strengths']==[]


def test_captured_technical_question_is_not_a_freeform_question_protocol(captured_scope_failure):
    from app.services.resume_diagnostic_report import synthesis_facts, validate_synthesis
    raw=json.loads((Path(__file__).parent/'fixtures/resume_reading_technical_question_failure.json').read_text(encoding='utf8'))
    raw.pop('source_run')
    facts=synthesis_facts(build_resume_diagnostic_report(captured_scope_failure['profile'],student_id='scope-test'))
    with pytest.raises(ValueError):validate_synthesis(raw,facts)


def test_open_contribution_question_cannot_hide_a_preceding_ownership_assertion(captured_scope_failure):
    from app.services.resume_diagnostic_report import synthesis_facts,validate_synthesis
    facts=synthesis_facts(build_resume_diagnostic_report(captured_scope_failure['profile'],student_id='scope-test'))
    candidate=_reading_candidate(facts)
    candidate['questions'][0]['text']='你负责了前端展示，你实际参与了哪些工作？'
    with pytest.raises(ValueError):validate_synthesis(candidate,facts)


@pytest.mark.parametrize('focus,fragment_text',[
    ('contribution','我编写 SQL 分组查询统计各活动报名人数'),
    ('actual_task','SQL'),
])
def test_known_contribution_and_already_used_tool_are_not_reopened(focus,fragment_text,captured_scope_failure):
    from app.services.resume_diagnostic_report import synthesis_facts,validate_synthesis
    facts=synthesis_facts(build_resume_diagnostic_report(captured_scope_failure['profile'],student_id='scope-test'))
    candidate=_reading_candidate(facts)
    fact=next(f for f in facts if any(x['text']==fragment_text for x in f['fragments']))
    fragment=next(x for x in fact['fragments'] if x['text']==fragment_text)
    candidate['questions']=[{'focus':focus,'evidence_ids':[fact['id']],
                            'anchors':[{'evidence_id':fact['id'],'fragment_id':fragment['id']}]}]
    with pytest.raises(ValueError):validate_synthesis(candidate,facts)


def test_known_peer_contribution_is_excluded_before_generation(captured_scope_failure):
    from app.services.resume_diagnostic_report import synthesis_facts
    profile=deepcopy(captured_scope_failure['profile'])
    profile['projects'][0]['desc']='查询和核对由我编写，页面展示由同伴完成，用于课堂展示。'
    facts=synthesis_facts(build_resume_diagnostic_report(profile,student_id='scope-test'))
    project=next(f for f in facts if f['id']=='projects_0')
    known=next(f for f in project['fragments'] if '同伴' in f['text'])
    assert project['question_choices'], 'Useful unresolved choices remain, not just an empty rejection'
    assert not any(q['focus']=='contribution' and q['fragment_id']==known['id'] for q in project['question_choices'])


def test_role_field_resolves_checking_without_turning_every_tool_into_a_quiz(captured_scope_failure):
    from app.services.resume_diagnostic_report import synthesis_facts
    facts=synthesis_facts(build_resume_diagnostic_report(captured_scope_failure['profile'],student_id='scope-test'))
    project=next(f for f in facts if f['id']=='projects_0')
    checking=next(f for f in project['fragments'] if f['text']=='逐项核对重复报名')
    assert not any(q['focus']=='contribution' and q['fragment_id']==checking['id'] for q in project['question_choices'])
    assert not next(f for f in facts if f['id']=='skills_2')['question_choices']


def test_ambiguous_checking_keeps_exact_text_and_explicit_reading_scope(captured_scope_failure):
    from app.services.resume_diagnostic_report import synthesis_facts,synthesis_model
    facts=synthesis_facts(build_resume_diagnostic_report(captured_scope_failure['profile'],student_id='scope-test'))
    project=next(f for f in facts if f['id']=='projects_0')
    fragment=next(f for f in project['fragments'] if f['text']=='逐项核对重复报名')
    assert '不说明人工、自动或独立流程' in fragment['reading_boundary']
    assert project['fields']['desc']==captured_scope_failure['profile']['projects'][0]['desc']
    assert synthesis_model(facts).model_json_schema()['properties']['reading_notes']['maxItems']==0


def test_overview_starts_from_the_known_personal_task_not_an_ambiguous_function(captured_scope_failure):
    from app.services.resume_diagnostic_report import synthesis_facts,synthesis_model,validate_synthesis
    facts=synthesis_facts(build_resume_diagnostic_report(captured_scope_failure['profile'],student_id='scope-test'))
    schema=synthesis_model(facts).model_json_schema()
    anchor=schema['$defs']['RecordedPersonalTaskAnchor']['properties']['fragment_id']
    permitted=anchor.get('enum') or [anchor['const']]
    assert permitted==['projects_0.desc/f0']
    assert schema['$defs']['RecordedPersonalTaskReading']['properties']['anchors']['maxItems']==1
    assert '逐项核对重复报名' in json.dumps(facts,ensure_ascii=False), 'Do not hide the unresolved source'
    candidate=_reading_candidate(facts)
    candidate['what_is_clear']=candidate['summary']
    with pytest.raises(ValueError):validate_synthesis(candidate,facts)  # two conflicting aliases


def test_one_specific_narrative_correction_preserves_source_and_question(captured_scope_failure):
    from app.services.resume_diagnostic_report import synthesis_facts
    report=build_resume_diagnostic_report(captured_scope_failure['profile'],student_id='scope-test')
    good=_reading_candidate(synthesis_facts(report))
    bad=deepcopy(good);bad['summary']['text']='学生用 SQL 编写了含 GROUP BY 的查询。'
    calls=[]
    class Client:
        def chat_with_metadata(self,**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(text=json.dumps(bad if len(calls)==1 else good,ensure_ascii=False),
                finish_reason='stop',context_status=SimpleNamespace(fits=True),
                usage={'prompt_tokens':100,'completion_tokens':50,'total_tokens':150},model='deterministic')
    result=_synthesize(report,llm_client=Client())
    assert len(calls)==2, 'One grounded correction, not another user submission or an answer-selection loop'
    assert result['analysis'] is not None and result['model_usage']['total_tokens']==300
    assert calls[0]['messages'][1]==calls[1]['messages'][1]
    assert '【正文纠正反馈】' in calls[1]['messages'][0]['content']
    schema=calls[1]['response_format']['json_schema']['schema']
    definition=schema['properties']['what_is_clear']['$ref'].rsplit('/',1)[1]
    import re
    assert re.search(schema['$defs'][definition]['properties']['text']['not']['pattern'],'GROUP BY')
    assert result['analysis']['questions'][0]['text']==result['improvements'][0]['questions'][0]


def test_supported_task_clause_is_not_mistaken_for_a_new_responsibility(captured_scope_failure):
    from app.services.resume_diagnostic_report import synthesis_facts,validate_synthesis
    facts=synthesis_facts(build_resume_diagnostic_report(captured_scope_failure['profile'],student_id='scope-test'))
    candidate=_reading_candidate(facts)
    text='学生用 SQL 编写了分组查询，目的是统计每个校园活动的报名人数；这个任务的对象是各活动，读者比只看到SQL这个词更清楚查询在统计什么。'
    candidate['summary']['text']=text
    assert validate_synthesis(candidate,facts)['summary']['text']==text
    for bad in ['学生用 SQL 独立编写了分组查询和前端展示。','学生用 SQL 编写了全部前端页面。']:
        candidate['summary']['text']=bad
        with pytest.raises(ValueError):validate_synthesis(candidate,facts)
    candidate['summary']['text']=text
    for original in ['我没有编写 SQL 分组查询。','我计划编写 SQL 分组查询。','我正在编写 SQL 分组查询，尚未完成。']:
        profile=deepcopy(captured_scope_failure['profile'])
        profile['projects'][0]['desc']=original+'；用 Vue 展示查询结果。'
        other=synthesis_facts(build_resume_diagnostic_report(profile,student_id='scope-test'))
        with pytest.raises(ValueError):validate_synthesis(candidate,other)


@pytest.mark.parametrize('text',[
    'SQL 查询与核对任务的分工已由角色字段明确；需要讲清展示部分。',
    '该用途未锚定在具体分工上，需确认用途对应的工作产出。',
    '展示可能是协作、学习实践或仅了解界面效果。',
])
def test_reader_summary_is_not_internal_metadata_or_speculation_options(text,captured_scope_failure):
    from app.services.resume_diagnostic_report import synthesis_facts,validate_synthesis
    facts=synthesis_facts(build_resume_diagnostic_report(captured_scope_failure['profile'],student_id='scope-test'))
    candidate=_reading_candidate(facts);candidate['summary']['text']=text
    with pytest.raises(ValueError):validate_synthesis(candidate,facts)


def test_purpose_statement_is_not_a_new_unassigned_action(captured_scope_failure):
    from app.services.resume_diagnostic_report import synthesis_facts
    p=deepcopy(captured_scope_failure['profile']);p['projects'][0]['desc']='查询和核对由我编写，页面由同伴完成，用于课堂展示。'
    fact=next(f for f in synthesis_facts(build_resume_diagnostic_report(p,student_id='scope-test')) if f['id']=='projects_0')
    purpose=next(x for x in fact['fragments'] if x['text']=='用于课堂展示')
    assert not any(q['fragment_id']==purpose['id'] for q in fact['question_choices'])


def test_bad_narrative_does_not_discard_an_independently_bound_useful_question(captured_scope_failure):
    from app.services.resume_diagnostic_report import synthesis_facts
    report=build_resume_diagnostic_report(captured_scope_failure['profile'],student_id='scope-test')
    candidate=_reading_candidate(synthesis_facts(report))
    candidate['summary']['text']='重复报名核对被列为独立动作且纳入职责范围，说明该环节需人工判断，非自动化结果输出。'
    class Client:
        def chat_with_metadata(self,**kw):
            return SimpleNamespace(text=json.dumps(candidate,ensure_ascii=False),finish_reason='stop',
                context_status=SimpleNamespace(fits=True),usage={},model='deterministic')
    result=_synthesize(report,llm_client=Client())
    assert result['analysis'] is None and result['analysis_status']=='invalid_grounded_interpretation'
    assert result['status']=='partial' and 'evidence_grounded_semantic_analysis' in result['missing_sections']
    assert result['improvements_status']=='ready' and len(result['improvements'])==1
    assert '用 Vue 展示查询结果' in result['improvements'][0]['questions'][0]


@pytest.mark.parametrize('surface',['summary','strengths','questions'])
@pytest.mark.parametrize('claim',[
    '您完成了 SQL 查询与 Vue 展示，具备端到端小流程实践线索。',
    '你独立完成了查询和前端展示。',
    'Vue 实践的真实颗粒度如何？',
    '人工核对说明具备数据治理意识。',
])
def test_anchored_claims_cannot_expand_actions_or_use_empty_jargon(surface,claim,captured_scope_failure):
    from app.services.resume_diagnostic_report import validate_synthesis, synthesis_facts
    report=build_resume_diagnostic_report(captured_scope_failure['profile'],student_id='scope-test')
    facts=synthesis_facts(report)
    fact=next(item for item in facts if item['id']=='projects_0')
    fragment=next(f for f in fact['fragments'] if 'SQL' in f['text'])
    point={'text':claim,'evidence_ids':['projects_0'],
           'anchors':[{'evidence_id':'projects_0','fragment_id':fragment['id']}]}
    candidate={'summary':{**point,'text':'分组统计可以看清各活动有多少人报名。'},'strengths':[],'questions':[]}
    if surface=='summary':candidate['summary']=point
    else:candidate[surface]=[point]
    with pytest.raises(ValueError):
        validate_synthesis(candidate,facts)


def test_source_field_warnings_are_not_report_facts():
    profile = _profile()
    profile["source_field_issues"] = [{"field": "grade", "reason": "not_explicit_in_source"}]
    report = build_resume_diagnostic_report(profile, student_id="student_test")
    assert "source_field_issues" not in json.dumps(report, ensure_ascii=False)
    assert "not_explicit_in_source" not in json.dumps(report, ensure_ascii=False)


def test_source_projection_keeps_exact_scope_and_rejects_cross_fact_anchor(captured_scope_failure):
    from app.services.resume_diagnostic_report import synthesis_facts, validate_synthesis
    profile=deepcopy(captured_scope_failure['profile'])
    profile['projects'][0]['desc']='查询由我独立编写，页面展示由同伴完成。'
    facts=synthesis_facts(build_resume_diagnostic_report(profile,student_id='scope-test'))
    fact=next(item for item in facts if item['id']=='projects_0')
    fragment=next(f for f in fact['fragments'] if f['text']=='查询由我独立编写')
    point={'text':'查询可用来整理报名记录。','evidence_ids':['projects_0'],
           'anchors':[{'evidence_id':'projects_0','fragment_id':fragment['id']}]}
    candidate={'summary':point,'strengths':[],'questions':[]}
    before=deepcopy(candidate)
    result=validate_synthesis(candidate,facts)
    assert result['summary']['source_quotes'][0]['text']=='查询由我独立编写'
    assert result['summary']['text']==point['text']
    assert '页面展示' not in result['summary']['text']
    assert candidate==before and 'anchors' not in result['summary']
    candidate['summary']['anchors'][0]['evidence_id']='skills_0'
    candidate['summary']['evidence_ids']=['skills_0']
    with pytest.raises(ValueError,match='片段'):
        validate_synthesis(candidate,facts)


def test_known_manual_action_stays_in_quote_and_input_budget_never_silently_slices(captured_scope_failure):
    from app.services.resume_diagnostic_report import synthesis_facts, validate_synthesis
    profile=deepcopy(captured_scope_failure['profile'])
    profile['projects'][0]['desc']='我人工核对重复名单。'
    facts=synthesis_facts(build_resume_diagnostic_report(profile,student_id='scope-test'))
    fact=next(item for item in facts if item['id']=='projects_0')
    fragment=next(f for f in fact['fragments'] if f['text']=='我人工核对重复名单')
    point={'text':'核对重复记录可避免同一人重复计入。','evidence_ids':['projects_0'],
           'anchors':[{'evidence_id':'projects_0','fragment_id':fragment['id']}]}
    assert '我人工核对重复名单' == validate_synthesis({'summary':point,'strengths':[],'questions':[]},facts)['summary']['source_quotes'][0]['text']
    profile['projects'][0]['desc']='不得静默丢失的原文'*4000
    class NeverCalled:
        def chat_with_metadata(self,**kwargs): raise AssertionError('budget must reject before model')
    report=_synthesize(build_resume_diagnostic_report(profile,student_id='scope-test'),llm_client=NeverCalled())
    assert report['analysis_status']=='input_budget_exceeded' and report['analysis'] is None
    assert report['chapters'][2]['items'][0]['fields']['desc']==profile['projects'][0]['desc']


def test_semantic_fact_names_in_role_rationale_are_student_facing():
    profile = _profile()
    profile["semantic_facts"] = {"team_project": {"value": True, "evidence": "检索项目"}}

    class Client:
        def chat_with_metadata(self, **kwargs):
            return SimpleNamespace(text=json.dumps({"directions": [{
                "title": "后端开发方向", "evidence_ids": ["team_project_0"],
                "questions_to_verify": []}]}), finish_reason="stop",
                context_status=SimpleNamespace(fits=True), usage={"total_tokens": 30}, model="test-model")

    report = _synthesize_role_directions(build_resume_diagnostic_report(profile, student_id="student_test"), llm_client=Client())
    role = next(ch for ch in report["chapters"] if ch["id"] == "role_directions")["items"][0]
    assert "team_project" not in role["fields"]["rationale"]
    assert "团队项目经历" in role["fields"]["rationale"]


def _profile():
    return {
        "name": "张同学", "school": "测试大学", "major": "计算机科学",
        "projects": [{"name": "检索项目", "role": "开发", "desc": "实现检索项目"}],
        "research": [{"name": "论文甲", "level": "校级"}],
        "competitions": [{"name": "竞赛甲", "award": "二等奖"}],
        "leadership_roles": ["项目组长"], "skills": ["Python"],
        "source": "canonical_llm", "raw_text_chars": 100,
        "structure_calls": [{"usage": {"total_tokens": 100}}],
        "entity_extraction_call": {"usage": {"total_tokens": 30}},
        "entity_extraction_status": "partial", "entity_extraction_issues": [{"index": 1}],
        "entity_linking": {"status": "proposed", "mentions": [
            {"name": "Python", "evidence": "Python"}], "candidates": [
            {"name": "Python 语言"}], "decisions": [
            {"mention_index": 0, "decision": "related", "candidate_index": 0,
             "rationale": "同一语言线索"}]},
        "_resume_confirmation": {"task_id": "l0_test", "student_id": "student_test",
            "source_evidence": {"file_id": "file_test", "page_count": 2,
                "observed_pages": [1, 2], "missing_pages": [], "accuracy_verified": False,
                "blocks": [
                    {"block_id": "b1", "page": 1, "text": "测试大学 计算机科学 Python 项目组长"},
                    {"block_id": "b2", "page": 2, "text": "检索项目 实现检索项目 论文甲 竞赛甲"}]}}
    }


def test_resume_report_preserves_all_facts_without_job_claims():
    report = build_resume_diagnostic_report(_profile(), student_id="student_test")
    assert report["status"] == "partial"  # factual draft is not semantic completion
    assert report["report_type"] == "resume_diagnostic"
    chapters = {x["id"]: x for x in report["chapters"]}
    assert len(chapters["experience"]["items"]) == 2
    assert len(chapters["achievements"]["items"]) == 2
    assert chapters["entity_review"]["items"][0]["fields"]["review_status"] == "proposed_not_confirmed"
    assert chapters["role_directions"]["status"] == "pending_model_interpretation"
    assert chapters["experience"]["items"][0]["evidence_refs"][0]["page"] == 2
    text = json.dumps(report, ensure_ascii=False)
    assert "raw_text_chars" not in text and "canonical_llm" not in text
    assert "entity_extraction_call" not in text and "structure_calls" not in text
    assert "match_score" not in text and "salary" not in text


def test_unmapped_school_tier_remains_quoted_but_not_presented_as_scoreable():
    profile = _profile()
    profile["semantic_facts"] = {"school_tier": {
        "value": "其他理工", "evidence": "其他理工", "source_sha256": "source-hash",
    }}
    profile["semantic_fact_issues"] = [{
        "field": "school_tier", "reason": "value_not_in_scoring_category",
    }]
    profile["semantic_fact_next_action"] = "review_unmapped_scoring_category"
    profile["_resume_confirmation"]["source_evidence"]["blocks"][0]["text"] += " 其他理工"

    report = build_resume_diagnostic_report(profile, student_id="student_test")
    chapter = next(item for item in report["chapters"] if item["id"] == "semantic_evidence")
    school = next(item for item in chapter["items"] if item["id"] == "school_tier_0")
    assert school["fields"]["value"] == "其他理工"
    assert school["evidence_refs"][0]["quote"] == "其他理工"
    assert school["scoring_status"] == "unmapped_category"
    assert any("学校层级" in item and "不可评分" in item for item in report["limitations"])


def test_semantic_synthesis_requires_existing_evidence_ids():
    report = build_resume_diagnostic_report(_profile(), student_id="student_test")
    class Client:
        def chat_with_metadata(self, **kwargs):
            payload={
                "summary": {"text": "有计算机项目与竞赛经历。", "evidence_ids": ["projects_0", "competitions_0"]},
                "strengths": [{"text": "有项目实践线索。", "evidence_ids": ["projects_0"]}],
                "questions": []}
            facts={item['id']:item for item in json.loads(kwargs['messages'][1]['content'])}
            for point in [payload['summary'],*payload['strengths']]:
                point['anchors']=[{'evidence_id':key,'fragment_id':facts[key]['fragments'][-1]['id']}
                                  for key in point['evidence_ids']]
            return SimpleNamespace(text=json.dumps(payload, ensure_ascii=False), finish_reason="stop",
                context_status=SimpleNamespace(fits=True), usage={"total_tokens": 300}, model="test-model")
    report = _synthesize(report, llm_client=Client())
    assert report["status"] == "partial"  # role directions still missing
    assert report["analysis_status"] == "model_interpretation_with_references"

    class Hallucinating(Client):
        def chat_with_metadata(self, **kwargs):
            result = super().chat_with_metadata(**kwargs)
            data = json.loads(result.text)
            data["summary"]["evidence_ids"] = ["nonexistent"]
            result.text = json.dumps(data)
            return result
    invalid = _synthesize(build_resume_diagnostic_report(_profile(), student_id="student_test"),
                          llm_client=Hallucinating())
    assert invalid["status"] == "partial"
    assert invalid["analysis"] is None

    class UnverifiableAbility(Client):
        def chat_with_metadata(self, **kwargs):
            result=super().chat_with_metadata(**kwargs)
            data=json.loads(result.text)
            data['strengths'][0]['text']='熟练掌握Python，胜任全栈开发'
            result.text=json.dumps(data)
            return result
    invalid=_synthesize(build_resume_diagnostic_report(_profile(),student_id='student_test'),llm_client=UnverifiableAbility())
    assert invalid['analysis'] is None and invalid['analysis_status']=='unsupported_ability_claim'


def test_role_directions_are_evidence_grounded_but_not_catalog_jobs():
    class Client:
        def chat_with_metadata(self, **kwargs):
            assert "张同学" not in kwargs["messages"][1]["content"]
            return SimpleNamespace(text=json.dumps({"directions": [{
                "title": "检索应用开发方向",
                "evidence_ids": ["projects_0"],
                "questions_to_verify": ["实际承担的职责是什么？"]}]}, ensure_ascii=False),
                finish_reason="stop", context_status=SimpleNamespace(fits=True),
                usage={"total_tokens": 123}, model="test-model")
    report = _synthesize_role_directions(build_resume_diagnostic_report(_profile(),
                                                                          student_id="student_test"),
                                         llm_client=Client())
    chapter = next(ch for ch in report["chapters"] if ch["id"] == "role_directions")
    assert chapter["status"] == "model_interpretation_unverified"
    assert chapter["items"][0]["fields"]["source_type"] == "resume_model_inference"
    assert chapter["items"][0]["evidence_refs"][0]["page"] == 2
    assert "resume_based_role_directions" not in report["missing_sections"]
    assert "job_portrait_id" not in json.dumps(chapter)

    class Invalid(Client):
        def chat_with_metadata(self, **kwargs):
            result = super().chat_with_metadata(**kwargs)
            payload = json.loads(result.text)
            payload["directions"][0]["evidence_ids"] = ["absent"]
            result.text = json.dumps(payload)
            return result
    bad = _synthesize_role_directions(build_resume_diagnostic_report(_profile(),
                                                                       student_id="student_test"),
                                      llm_client=Invalid())
    assert next(ch for ch in bad["chapters"] if ch["id"] == "role_directions")["items"] == []
    assert "resume_based_role_directions" in bad["missing_sections"]


def test_role_direction_citations_follow_model_fact_order():
    class Client:
        def chat_with_metadata(self, **kwargs):
            return SimpleNamespace(text=json.dumps({"directions": [{
                "title": "检索方向", "evidence_ids": ["skills_0", "projects_0"],
                "questions_to_verify": ["希望专注哪类岗位？"]}]}),
                finish_reason="stop", context_status=SimpleNamespace(fits=True),
                usage={"total_tokens": 40}, model="test-model")

    report = _synthesize_role_directions(
        build_resume_diagnostic_report(_profile(), student_id="student_test"),
        llm_client=Client(),
    )
    role = next(ch for ch in report["chapters"] if ch["id"] == "role_directions")["items"][0]
    assert [ref["page"] for ref in role["evidence_refs"][:2]] == [1, 2]
    assert role["fields"]["evidence_ids"] == ["skills_0", "projects_0"]


def test_old_report_get_preserves_facts_and_explicitly_requests_upgrade(monkeypatch):
    import app.services.resume_diagnostic_report as service
    profile = _profile()
    legacy = build_resume_diagnostic_report(profile, student_id="student_test")
    legacy["chapters"] = [ch for ch in legacy["chapters"] if ch["id"] != "role_directions"]
    legacy["status"] = "completed"
    legacy["missing_sections"] = []
    row = {"report_id": legacy["report_id"], "student_id": "student_test",
           "source_confirmation_task_id": "l0_test", "status": "completed",
           "report_json": canonical_json(legacy), "content_sha256": content_sha256(legacy)}
    class DB:
        def fetch_all(self, sql, params):
            return [row]
    monkeypatch.setattr(service, "require_confirmed_profile", lambda _db, _sid: profile)
    catalog_version = ["empty-v1"]
    monkeypatch.setattr("app.services.job_catalog_listing.list_published_job_candidates",
                        lambda *args, **kwargs: {"status": "empty", "total": 0,
                                                "items": [], "catalog_version": catalog_version[0]})
    result = read_resume_diagnostic(DB(), student_id="student_test")
    assert result['analysis_content_status']=='legacy'
    assert row['report_json']==canonical_json(legacy)
    assert result["status"] == "partial"
    assert next(ch for ch in result["chapters"] if ch["id"] == "role_directions")["status"] == "pending_upgrade"
    assert next(ch for ch in result["chapters"] if ch["id"] == "experience")["items"]
    assert result["job_catalog"]["status"] == "empty"
    assert "resume_based_role_directions" in result["missing_sections"]
    catalog_version[0] = "empty-v2"
    refreshed = read_resume_diagnostic(DB(), student_id="student_test")
    assert refreshed["job_catalog"]["catalog_version"] == "empty-v2"
    assert refreshed["chapters"] == result["chapters"]
