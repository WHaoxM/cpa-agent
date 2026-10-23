"""No model substitutes for acceptance: these assert the model input boundary."""
import json
import pytest
from app.services.agent_runtime.task_evidence_projection import PREFIX, project_exploration_packet, exploration_invoke
from app.services.agent_runtime.task_packet import build_standard_packet


def sample():
    bank = [{'id':'resume.project.0','kind':'resume_experience','quote':'成员；核对重复行',
             'fragments':[{'id':'resume.project.0/f0','text':'成员'},{'id':'resume.project.0/f1','text':'核对重复行'}]},
            {'id':'student.previous.0','kind':'student_statement','quote':'我没有负责前端'},
            {'id':'student.message','kind':'student_statement','quote':'本周四小时'}]
    practice = {'student_practice_not_verified_facts':[{'step':{'completed':True,'practice_record':{
        'summary':'两行重复报名，尚未解决','attachment':{'read_status':'partial','content':'实际读到片段'}}}}]}
    body = {'evidence':[
        {'section':'overview','data':{'compared_roles':['数据分析'],'plan_uri':'career://me/action-plans/owned'}},
        {'section':'facts','data':[{'source':'confirmed_resume','source_status':'current','projects':['核对重复行']},
            {'source':'student_self_report','self_report':'我没有负责前端'},
            {'source':'portrait_and_resume_interpretation','reading':{'portrait':{'large':'重复模型解释'*400}}}]},
        {'section':'directions','data':[{'role_name':'数据分析','skills':['SQL']}]},practice],
        'positioning_evidence':bank,'student_message':'本周四小时',
        'previous_discussion_not_verified_facts':[{'student_message':'我没有负责前端','assistant_question':'哪周尝试？','assistant_reply':'不作为事实的旧解释'*400}]}
    return body, build_standard_packet('qa',goal=PREFIX+json.dumps(body,ensure_ascii=False),write_keys=[],capability_id='career-exploration')


def test_projection_keeps_corrections_anchors_and_actual_attachment_coverage_without_duplicate_interpretations():
    original, packet = sample()
    projected = project_exploration_packet(packet)
    value = json.loads(projected.goal[len(PREFIX):])
    assert value['positioning_evidence'] == original['positioning_evidence'][1:]
    assert value['deferred_evidence_index'][0]['id'] == original['positioning_evidence'][0]['id']
    assert 'quote' not in value['deferred_evidence_index'][0]
    assert value['practice'][0]['student_practice_not_verified_facts'] == original['evidence'][3]['student_practice_not_verified_facts']
    assert value['output_contract']['operation'] == 'review_existing_trial'
    assert value['previous_questions_not_facts'] == ['哪周尝试？']
    assert value['projection_scope']['available_interpretations'] == ['portrait']
    assert '未注入' in value['projection_scope']['interpretations']
    assert '重复模型解释' not in projected.goal and '不作为事实的旧解释' not in projected.goal
    assert len(projected.goal) < len(packet.goal) / 2
    assert json.loads(packet.goal[len(PREFIX):]) == original


def test_unknown_input_contract_is_rejected_instead_of_silently_dropping_new_fields():
    body, packet = sample()
    body['new_authoritative_facts'] = '不可丢弃'
    packet.goal = PREFIX + json.dumps(body)
    with pytest.raises(ValueError,match='契约已变化'):
        project_exploration_packet(packet)


def test_context_preparation_precedes_input_guard_and_keeps_original_bank_for_validation(monkeypatch):
    from app.services import career_exploration_analysis as analysis
    body,packet=sample()
    body['evidence'][1]['data'][2]['reading']['portrait']={'large':'旧模型解释'*6000}
    assert len(json.dumps(body,ensure_ascii=False))>18000
    seen=[]
    def validate(value,roles,**kwargs):
        assert kwargs['positioning_evidence']==body['positioning_evidence']
        assert kwargs['positioning_evidence'][0]['quote']=='成员；核对重复行'
        seen.append(True)
    monkeypatch.setattr(analysis,'validate_analysis',validate)
    scoped=exploration_invoke(lambda p,*a,**k:{'reply':'{"valid":true}'})
    prepared=scoped.prepare_context(body)
    encoded=json.dumps(prepared,ensure_ascii=False)
    assert len(encoded)<18000 and '旧模型解释' not in encoded
    packet.goal=PREFIX+encoded
    result=scoped(packet)
    assert seen==[True]
    assert result['task_context_projection']['original_chars']>18000
    assert result['task_context_projection']['model_chars']<18000


def test_incomplete_fragment_split_cannot_discard_a_short_student_correction():
    body,packet=sample()
    body['positioning_evidence'][1].update(quote='是。不是前端。',fragments=[{'id':'answer/f0','text':'不是前端'}])
    packet.goal=PREFIX+json.dumps(body,ensure_ascii=False)
    value=json.loads(project_exploration_packet(packet).goal[len(PREFIX):])
    assert next(r for r in value['positioning_evidence'] if r['id']=='student.previous.0')['quote'] == '是。不是前端。'


def test_direction_exploration_keeps_resume_anchor_fragments_not_just_a_directory():
    body,packet=sample();body['evidence'][0]['data']['plan_uri']=None
    packet.goal=PREFIX+json.dumps(body,ensure_ascii=False)
    value=json.loads(project_exploration_packet(packet).goal[len(PREFIX):])
    assert value['positioning_evidence'][0]['fragments']==body['positioning_evidence'][0]['fragments']
    assert value['deferred_evidence_index']==[]
    assert value['projection_scope']['receipt_names']==['本次探索','本人经历与自述','已选方向资料','已确认简历']


def test_model_tool_help_projection_changes_no_validation_or_execution_schema():
    from app.services.agent_runtime.scoped_invoke import _career_model_tool_views
    from app.services.langgraph_agent import _adapt_existing_tool
    from app.services.tools.career_data_read import CareerDataReadTool
    from langchain_core.utils.function_calling import convert_to_openai_tool
    source=_adapt_existing_tool(CareerDataReadTool())
    original=convert_to_openai_tool(source)
    view=_career_model_tool_views([source])[0]
    def protocol(value):
        if isinstance(value,dict):return {k:protocol(v) for k,v in value.items() if k!='description'}
        if isinstance(value,list):return [protocol(item) for item in value]
        return value
    assert protocol(view)==protocol(original)
    assert convert_to_openai_tool(source)==original, 'Original gated tool metadata is untouched'
    assert len(json.dumps(view,ensure_ascii=False).encode('utf8'))<len(json.dumps(original,ensure_ascii=False).encode('utf8'))


def test_review_phase_cannot_be_used_as_a_permission_or_other_capability_override():
    from app.services.agent_runtime.blackboard import BlackboardError
    packet=build_standard_packet('qa',goal='只读',write_keys=[])
    packet.business_phase='practice_review'
    with pytest.raises(BlackboardError,match='business phase'):
        packet.validate()


def test_repeated_question_is_one_input_but_conflicting_student_correction_survives():
    body,packet=sample()
    body['positioning_evidence'].insert(-1,{'id':'student.previous.1','kind':'student_statement','quote':'本周四小时'})
    body['positioning_evidence'].insert(-1,{'id':'student.previous.2','kind':'student_statement','quote':'更正，本周两小时'})
    packet.goal=PREFIX+json.dumps(body,ensure_ascii=False)
    value=json.loads(project_exploration_packet(packet).goal[len(PREFIX):])
    assert sum(r.get('quote')=='本周四小时' for r in value['positioning_evidence'])==1
    assert any(r.get('quote')=='更正，本周两小时' for r in value['positioning_evidence'])
    assert value['identical_statement_aliases']==[{'id':'student.previous.1','same_statement_as':'student.message'}]


def test_preflight_stop_is_not_reclassified_as_a_generic_invalid_analysis():
    _,packet=sample()
    with pytest.raises(RuntimeError,match='turn_token_cap_exceeded'):
        exploration_invoke(lambda *a,**k:{'incomplete':True,'stop_reason':'turn_token_cap_exceeded'})(packet)


def test_projection_invokes_existing_runner_with_same_ledger_and_tool_permissions(monkeypatch):
    from app.services import career_exploration_analysis as analysis
    monkeypatch.setattr(analysis,'validate_analysis',lambda *a,**k:None)
    from app.services.agent_runtime.budget import capture_usage_ledger,current_usage_ledger
    _, packet = sample()
    with capture_usage_ledger('root',turn_token_cap=30000) as ledger:
        def invoke(p,*args,**kwargs):
            assert current_usage_ledger() is ledger
            assert kwargs == {'allow_tools':True,'plan_tool_allowlist':{'career_data_read'}}
            assert p.capability_id == packet.capability_id
            return {'reply':'{"candidate":"仍需原有校验"}'}
        result = exploration_invoke(invoke)(packet,allow_tools=True,plan_tool_allowlist={'career_data_read'})
        assert json.loads(result['reply'])['candidate'] == '仍需原有校验'


def test_only_one_structure_repair_uses_compact_grounded_input_and_same_ledger(monkeypatch):
    from app.services import career_exploration_analysis as analysis,career_exploration as domain
    from app.services.agent_runtime.budget import capture_usage_ledger,current_usage_ledger
    checks=[];repairs=[]
    def validate(value,roles,**kwargs):
        checks.append(value)
        assert roles == ['数据分析'] and kwargs['require_grounding'] is True
        if value != {'valid':True}:raise ValueError('方向不符')
    monkeypatch.setattr(analysis,'validate_analysis',validate)
    _,packet=sample()
    with capture_usage_ledger('root',turn_token_cap=30000) as ledger:
        def repair(instruction,body,**kwargs):
            assert current_usage_ledger() is ledger
            assert kwargs == {'max_tokens':2400}
            assert '重复模型解释' not in json.dumps(body,ensure_ascii=False)
            assert body['read_evidence_and_student_message']['positioning_evidence']
            repairs.append(body)
            return {'valid':True}
        monkeypatch.setattr(domain,'model_json',repair)
        result=exploration_invoke(lambda *a,**k:{'reply':'{"invalid":true}'})(packet)
        assert json.loads(result['reply']) == {'valid':True}
        assert len(repairs) == 1 and len(checks) == 2


def test_invalid_repair_stops_before_domain_can_start_another_repair(monkeypatch):
    from app.services import career_exploration_analysis as analysis,career_exploration as domain
    calls=[]
    def reject(*a,**k):raise ValueError('原文锚点不符')
    monkeypatch.setattr(analysis,'validate_analysis',reject)
    monkeypatch.setattr(domain,'model_json',lambda *a,**k:calls.append(True) or {})
    _,packet=sample()
    with pytest.raises(RuntimeError,match='task_answer_validation_failed'):
        exploration_invoke(lambda *a,**k:{'reply':'{}'})(packet)
    assert calls == [True]


def test_review_recovery_repairs_only_invalid_fields_without_resending_resume(monkeypatch):
    from app.services import career_exploration as domain
    from app.services.agent_runtime.budget import capture_usage_ledger,current_usage_ledger
    _,packet=sample();calls=[]
    review={'observed':'本人记录了合成数据查询','difficulties':'向别人说明仍有困难','uncertain':'','next_options':'本人考虑调整方式'}
    candidate={'reply':'依据本人练习复盘','question':None,'comparisons':[],'positioning':None,'proposal':None,'review':review}
    with capture_usage_ledger('root',turn_token_cap=30000) as ledger:
        def repair(instruction,body,**kwargs):
            assert current_usage_ledger() is ledger
            assert body['repair_fields']==['uncertain'] and kwargs['max_tokens']==900
            assert 'resume.project' not in json.dumps(body)
            assert body['valid_review_not_verified_facts']['difficulties']==review['difficulties']
            calls.append(body)
            return {'uncertain':'尚不能据此判断能力或是否适合该方向'}
        monkeypatch.setattr(domain,'model_json',repair)
        result=exploration_invoke(lambda *a,**k:{'reply':json.dumps(candidate,ensure_ascii=False)})(packet)
    value=json.loads(result['reply'])
    assert all(value['review'][k]==v for k,v in review.items() if k!='uncertain')
    assert result['task_context_recovery']=={'kind':'review_fields','attempts':1,'repaired_fields':['uncertain']}
    assert result['task_context_projection']['model_chars']<result['task_context_projection']['original_chars']
    assert len(calls)==1


def test_system_read_boundary_is_not_published_as_a_student_gap(monkeypatch):
    from app.services import career_exploration as domain
    _,packet=sample();calls=[]
    candidate={'reply':'依据本人记录复盘','question':None,'comparisons':[],'positioning':None,'proposal':None,
        'review':{'observed':'本人提交了查询结果','difficulties':'向别人解释还不熟悉',
        'uncertain':'尚无材料说明你是否已读过简历技能列表或方向目录正文','next_options':'本人选择下一步'}}
    def repair(instruction,body,**kwargs):
        calls.append(body['repair_fields'])
        return {'uncertain':'第三步还没有提交讲解或听众反馈，尚不能判断解释效果'}
    monkeypatch.setattr(domain,'model_json',repair)
    result=exploration_invoke(lambda *a,**k:{'reply':json.dumps(candidate,ensure_ascii=False)})(packet)
    assert calls==[['uncertain']]
    assert json.loads(result['reply'])['review']['difficulties']==candidate['review']['difficulties']


def test_existing_trial_review_does_not_revalidate_ineligible_personal_positioning(monkeypatch):
    from app.services import career_exploration_analysis as analysis,career_exploration as domain
    accepted=[]
    def validate(value,roles,**kwargs):
        assert value['review'] == {'observed':'本人文字','difficulties':'待说明','uncertain':'仍未知','next_options':'本人选择'}
        assert value['positioning'] is None and value['proposal'] is None and value['comparisons'] == []
        accepted.append(True)
    monkeypatch.setattr(analysis,'validate_analysis',validate)
    monkeypatch.setattr(domain,'model_json',lambda *a,**k:pytest.fail('valid review does not need a second model call'))
    _,packet=sample()
    candidate={'reply':'复盘','question':None,'comparisons':[{'role':'另一个方向'}],
        'proposal':{'invalid':'不属于现有计划'},'positioning':{'anchors':['额外片段']*4},
        'review':{'observed':'本人文字','difficulties':'待说明','uncertain':'仍未知','next_options':'本人选择'}}
    result=exploration_invoke(lambda *a,**k:{'reply':json.dumps(candidate,ensure_ascii=False)})(packet)
    assert json.loads(result['reply'])['review'] == candidate['review']
    assert accepted == [True]
