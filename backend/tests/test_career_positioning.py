"""Personal positioning is interpretation of attributed evidence, not a score."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from app.services.career_positioning import CONTENT_VERSION, evidence_for_positioning, validate_positioning, experience_items


@pytest.fixture
def captured_clarification():
    return json.loads((Path(__file__).parent/'fixtures/career_clarification_failure.json').read_text(encoding='utf8'))


def test_actual_reading_padding_is_repaired_not_published_or_silently_dropped():
    from app.services.career_exploration_analysis import validate_analysis
    case=json.loads((Path(__file__).parent/'fixtures/career_reading_padding_failure.json').read_text(encoding='utf8'))
    with pytest.raises(ValueError,match='例子|技术考核') as failure:
        validate_analysis(case['candidate'],case['roles'],positioning_evidence=case['bank'],require_grounding=True)
    assert 'connections.0.text' in str(failure.value)
    assert 'uncertainties.0.text' in str(failure.value)
    assert 'next_action.reason' in str(failure.value)


def test_initial_start_reuses_one_current_source_question_without_freeform_examples():
    from app.services.career_positioning import bind_current_reading_questions,reading_question_options,positioning_output_schema
    from app.services.resume_improvements import reading_question
    from app.services.career_exploration_analysis import validate_analysis,answered_repair_format
    case=json.loads((Path(__file__).parent/'fixtures/career_reading_padding_failure.json').read_text(encoding='utf8'))
    bank=deepcopy(case['bank'])
    q={**reading_question('rule','逐项核对重复报名'),'evidence_ids':['projects_0'],
        'source_quotes':[{'evidence_id':'projects_0','text':'逐项核对重复报名'}]}
    reading={'status':'current','resume_interpretation':{'analysis_content_status':'current','analysis':{'questions':[q]}}}
    bind_current_reading_questions(bank,reading)
    options=reading_question_options(bank)
    assert list(options)==['resume-reading-0']
    candidate=deepcopy(case['candidate'])
    candidate['positioning'].update(connections=[],uncertainties=[],
        next_action={'kind':'clarify_experience','choice':'resume-reading-0'})
    result=validate_analysis(candidate,case['roles'],positioning_evidence=bank,require_grounding=True)
    assert result['positioning']['next_action']['text']==q['text']
    assert result['positioning']['next_action']['reason']==q['why']
    assert 'choice' not in result['positioning']['next_action']
    assert result['positioning']['uncertainties'][0]['text']==q['problem']
    assert len(positioning_output_schema(bank)['properties']['next_action']['anyOf'])==2
    assert positioning_output_schema(bank)['properties']['uncertainties']['maxItems']==0
    assert answered_repair_format(bank) is not None
    assert 'choice' not in case['candidate']['positioning']['next_action'], 'Raw failed prose was not overwritten'
    option=options['resume-reading-0']
    duplicate={'kind':'needs_detail','text':q['text'].replace('“','‘').replace('”','’'),
        'evidence_ids':[option['evidence_id']],
        'anchors':[{'evidence_id':option['evidence_id'],'fragment_id':option['fragment_id']}]}
    candidate['positioning']['uncertainties']=[duplicate]
    deduplicated=validate_analysis(candidate,case['roles'],positioning_evidence=bank,require_grounding=True)
    assert deduplicated==result, 'Only a literal copy of the SAME source question is canonicalized'
    for mutation in ('text','anchor','extra'):
        bad=deepcopy(candidate)
        if mutation=='text':bad['positioning']['uncertainties'][0]['text']+='比如按手机号？'
        elif mutation=='anchor':bad['positioning']['uncertainties'][0]['anchors'][0]['fragment_id']='other/f0'
        else:bad['positioning']['uncertainties'][0]['reason']='未校验内容'
        with pytest.raises(ValueError):validate_analysis(bad,case['roles'],positioning_evidence=bank,require_grounding=True)
    candidate['positioning']['uncertainties']=[]
    candidate['positioning']['next_action']['text']='替换为本人编造的问题'
    with pytest.raises(ValueError):validate_analysis(candidate,case['roles'],positioning_evidence=bank,require_grounding=True)
    for status in ('legacy','unavailable'):
        other=deepcopy(case['bank']);reading['resume_interpretation']['analysis_content_status']=status
        bind_current_reading_questions(other,reading)
        assert not reading_question_options(other)


def test_saved_clarification_failure_identifies_the_actual_unsafe_field(captured_clarification):
    from app.services.career_exploration_analysis import validate_analysis
    case=captured_clarification
    # The source already contains the student's rule and division of work.
    statement=next(e for e in case['bank'] if e['id']=='student.message')
    assert '姓名和手机号' in statement['quote'] and '由同伴完成' in statement['quote']
    with pytest.raises(ValueError,match=r'positioning\.next_action\.text:.*(?:个人职责|例子)'):
        validate_analysis(case['initial'],case['roles'],positioning_evidence=case['bank'],require_grounding=True)


def test_saved_three_anchor_correction_is_still_rejected(captured_clarification):
    from app.services.career_exploration_analysis import validate_analysis
    case=captured_clarification
    with pytest.raises(ValueError,match='at most 2'):
        validate_analysis(case['correction'],case['roles'],positioning_evidence=case['bank'],require_grounding=True)


def test_model_schema_and_repair_share_the_existing_hard_limits():
    from app.services.career_positioning import positioning_output_schema, positioning_output_constraints
    from app.services.career_exploration_analysis import analysis_validation_feedback, INSTRUCTION
    schema=positioning_output_schema()
    assert schema['properties']['anchors']['maxItems']==2
    assert schema['$defs']['_AnchoredPoint']['properties']['anchors']['maxItems']==3
    assert schema['additionalProperties'] is False
    constraints=positioning_output_constraints()
    assert json.loads(INSTRUCTION.split('【positioning结构约束】\n')[1])==constraints
    assert constraints['positioning.anchors']['maxItems']==schema['properties']['anchors']['maxItems']
    assert 'positioning.next_action.text' in analysis_validation_feedback(ValueError('positioning.next_action.text: 未核实的本人想法'))


def test_native_prose_boundary_blocks_invented_counts_not_technical_versions():
    import re
    from app.services.career_positioning import positioning_output_schema
    pattern=positioning_output_schema()['$defs']['_AnchoredNext']['properties']['text']['not']['pattern']
    assert re.search(pattern,'A活动有23人，B活动有17人')
    assert re.search(pattern,'例如某活动报名最多')
    assert not re.search(pattern,'说明使用Python3处理记录的结果')


def test_structural_feedback_retains_path_and_limit_without_echoing_candidate(captured_clarification):
    from app.services.career_exploration_analysis import analysis_validation_feedback
    case=captured_clarification
    try:
        validate_positioning(case['correction']['positioning'],case['roles'],case['bank'],require_grounding=True)
    except ValueError as exc:
        feedback=json.loads(analysis_validation_feedback(exc))
        assert feedback[0]['field']=='positioning.anchors'
        assert 'at most 2' in feedback[0]['message']
        assert 'student.message/f3' not in str(feedback)
    else:
        pytest.fail('Three anchors must remain invalid, not silently trimmed')
    assert len(case['correction']['positioning']['anchors'])==3


def test_complete_saved_initial_and_clarified_inputs_fit_unchanged_budget(captured_clarification):
    from app.services.career_exploration_analysis import INSTRUCTION
    from app.services.agent_runtime.budget import context_window_status
    inputs=captured_clarification['preflight_inputs']
    assert any('页面展示由同伴完成' in entry['goal'] for entry in inputs)
    for entry in inputs:
        # Use whole captured inputs, not shorter summaries or trimmed evidence.
        status=context_window_status('qwen','qwen-plus',[
            {'role':'system','content':INSTRUCTION},{'role':'user','content':entry['goal']}],2048)
        assert status.estimated_input_tokens+2048<=30000, (entry['stage'],status)


def test_real_initial_interpretation_cannot_turn_checking_into_manual_work(captured_clarification):
    from app.services.career_exploration_analysis import validate_analysis
    case=captured_clarification['unsupported_method']
    assert '人工核对' in case['candidate']['positioning']['starting_point']
    assert not any('人工' in row['quote'] for row in case['bank'])
    with pytest.raises(ValueError,match=r'positioning\.starting_point:.*实现方式'):
        validate_analysis(case['candidate'],case['roles'],positioning_evidence=case['bank'],require_grounding=True)


@pytest.mark.parametrize('text',['人工核对重复，是为了确保统计结果准确。','自动去重让这次统计更准确。'])
def test_implementation_assertions_stay_in_original_quotes(text):
    from app.services.career_positioning import check_plain_interpretation
    with pytest.raises(ValueError,match='实现方式'):
        check_plain_interpretation(text,bank=evidence_for_positioning(sources(),''))


def test_method_alternatives_are_questions_not_asserted_personal_methods():
    from app.services.career_positioning import check_plain_interpretation
    assert check_plain_interpretation('当时是人工比对名单，还是写了查询识别重复？')
    assert check_plain_interpretation('区分人工校验和查询识别，才能补清具体做法。')


def test_explicit_manual_experience_is_preserved_as_an_exact_fact_not_hidden():
    source=sources();source['facts']['projects'][0]['desc']='我人工核对重复名单。'
    bank=evidence_for_positioning(source,'')
    fragment=next(f for f in bank[0]['fragments'] if f['text']=='我人工核对重复名单')
    anchor={'evidence_id':'resume.project.0','fragment_id':fragment['id']}
    value={'role':'数据分析','anchors':[anchor],
        'starting_point':'核对重复记录有助于避免同一人被重复统计。',
        'connections':[],'uncertainties':[],
        'next_action':{'kind':'clarify_experience','text':'请说明判定重复记录的规则。',
            'reason':'了解规则后才能讨论统计结果。','evidence_ids':['resume.project.0'],'anchors':[anchor]}}
    result=validate_positioning(value,['数据分析'],bank,require_grounding=True)
    assert '我人工核对重复名单' in result['starting_point']


def test_validated_next_question_and_reason_survive_existing_history_projection():
    from app.services.career_exploration_analysis import discussion_history_turn
    result={'reply':'已有分组查询。\n下一步：说明重复报名的判定规则。','question':None,
        'positioning':{'content_version':CONTENT_VERSION,'next_action':{'kind':'clarify_experience',
            'text':'说明重复报名的判定规则。','reason':'避免把同一人的多次报名重复计入。'}}}
    before=deepcopy(result)
    projected=discussion_history_turn('请看我的起点',result)
    assert projected['assistant_question']==result['positioning']['next_action']['text']
    assert result['positioning']['next_action']['reason'] in projected['assistant_reply']
    assert projected['student_message']=='请看我的起点' and result==before
    bank=evidence_for_positioning(sources(),'我补清了规则。',[projected['student_message']])
    assert not any(result['positioning']['next_action']['reason'] in row['quote'] for row in bank)
    old=deepcopy(result);old['positioning']['content_version']=1
    assert discussion_history_turn('历史记录',old)['assistant_question'] is None
    old['positioning']['content_version']=2
    assert discussion_history_turn('旧版问过的问题',old)['assistant_question']==result['positioning']['next_action']['text']


def test_real_answered_question_cannot_be_reopened_as_the_priority(captured_clarification):
    from app.services.career_exploration_analysis import validate_analysis
    case=captured_clarification['repeated_clarification']
    with pytest.raises(ValueError,match=r'positioning\.next_action:.*已回答'):
        validate_analysis(case['candidate'],case['roles'],positioning_evidence=case['bank'],require_grounding=True)


def test_answered_question_gate_does_not_invent_joint_responsibility(captured_clarification):
    from app.services.career_positioning import answered_duplicate_check_question
    case=captured_clarification['repeated_clarification']
    assert answered_duplicate_check_question(case['bank'])
    text=next(e['quote'] for e in case['bank'] if e['id']=='student.message')
    for changed in [text.replace('查询和重复报名核对都是我独立写的','查询是我写的，核对由同伴完成'),
                    text.replace('查询和重复报名核对都是我独立写的','查询和重复报名核对都是我没有写过的'),
                    text.replace('我用姓名和手机号的组合识别重复报名','具体判定规则还没有说明'),
                    text.replace('我没有向别人讲解过统计结论','我已经向同学讲解过统计结论')]:
        bank=evidence_for_positioning(sources(),changed)
        assert not answered_duplicate_check_question(bank)


def test_answered_question_validation_does_not_supply_a_fixed_model_answer(captured_clarification):
    from app.services.career_exploration_analysis import validate_analysis
    case=captured_clarification['repeated_clarification']
    for text in ['用已有统计结果，简短说明统计了哪些活动、怎样处理重复报名。',
                 '把已有报名统计讲给同学听，请对方复述数字代表什么。']:
        candidate=deepcopy(case['candidate'])
        # This captured candidate also contains unrelated, unsupported
        # business-effect prose. The positive check varies the generated
        # action, not an invalid optional paragraph now sent to correction.
        candidate['positioning']['connections']=[]
        candidate['positioning']['uncertainties']=[]
        candidate['positioning']['next_action'].update(kind='practice',text=text,
            reason='说明统计对象与去重规则，让别人知道数字怎样得来。')
        result=validate_analysis(candidate,case['roles'],positioning_evidence=case['bank'],require_grounding=True)
        assert result['positioning']['next_action']['text']==text
        assert text in result['reply']


def test_answered_turn_correction_schema_matches_the_existing_stage_gate(captured_clarification):
    from app.services.career_exploration_analysis import answered_repair_format
    case=captured_clarification['repeated_clarification']
    wire=answered_repair_format(case['bank'])
    schema=wire['json_schema']['schema']
    assert schema['$defs']['_AnchoredNext']['properties']['kind']=={'type':'string','const':'practice'}
    assert schema['properties']['question']=={'type':'null'}
    assert schema['properties']['proposal']=={'type':'null'}
    assert 'reader_gain' in schema['$defs']['_AnchoredNext']['properties']
    assert 'not' in schema['$defs']['_AnchoredNext']['properties']['text']
    assert 'default' not in schema['$defs']['_AnchoredNext']['properties']['text']
    bank=deepcopy(case['bank'])
    next(e for e in bank if e['id']=='student.message')['quote']='查询由我写，其他分工和规则不清楚。'
    assert answered_repair_format(bank) is None


def test_answered_turn_scopes_the_first_generation_and_the_single_correction(captured_clarification):
    from app.services.career_exploration_analysis import analysis_instruction,INSTRUCTION
    case=captured_clarification['repeated_clarification']
    instruction=analysis_instruction(case['bank'])
    assert '【本轮已明确的回应范围】' in instruction
    assert '不重跑查询' in instruction
    # The unified task transports the same complete fragments without a
    # duplicate quote; its response scope must remain the same.
    projected=[{k:v for k,v in e.items() if k!='quote'} for e in case['bank']]
    assert analysis_instruction(projected)==instruction
    assert analysis_instruction(evidence_for_positioning(sources(),'规则还不清楚'))==INSTRUCTION


def test_personal_turn_cannot_supply_a_second_question_in_an_unused_field():
    from app.services.career_exploration_analysis import validate_analysis
    value={'reply':'不会使用未校验正文','question':'当时按什么规则识别重复？','comparisons':[],
        'proposal':None,'review':None,'positioning':anchored_candidate()}
    with pytest.raises(ValueError,match='question:.*null'):
        validate_analysis(value,['数据分析'],positioning_evidence=evidence_for_positioning(sources(),''),require_grounding=True)


def test_personal_generation_only_writes_the_canonical_body_once():
    from app.services.career_exploration_analysis import validate_analysis
    value={'reply':'','question':None,'comparisons':[],
        'proposal':None,'review':None,'positioning':anchored_candidate()}
    result=validate_analysis(value,['数据分析'],positioning_evidence=evidence_for_positioning(sources(),''),require_grounding=True)
    assert result['reply'].startswith(result['positioning']['starting_point'])
    assert result['positioning']['next_action']['text'] in result['reply']
    value['positioning']=None
    with pytest.raises(ValueError):validate_analysis(value,['数据分析'])


def test_real_practice_correction_cannot_invent_a_concrete_duplicate_record(captured_clarification):
    from app.services.career_exploration_analysis import validate_analysis
    case=captured_clarification['invented_practice_example']
    with pytest.raises(ValueError,match=r'positioning\.next_action\.text:.*(?:示例|例子)'):
        validate_analysis(case['candidate'],case['roles'],positioning_evidence=case['bank'],require_grounding=True)


def test_one_correction_receives_all_known_next_step_failures(captured_clarification):
    from app.services.career_exploration_analysis import validate_analysis, analysis_validation_feedback
    case=captured_clarification['invented_practice_example']
    with pytest.raises(ValueError) as error:
        validate_analysis(case['first_candidate'],case['roles'],positioning_evidence=case['bank'],require_grounding=True)
    feedback=analysis_validation_feedback(error.value)
    assert '不要杜撰示例人数' in feedback
    assert '已回答' in feedback


@pytest.mark.parametrize('text',[
    '用已有统计结果说明，例如“张三用两个手机号报了A活动”。',
    '用已有结果解释，比如张三换号重复报名。',
    '举例说“某同学报名了两次”，再讲怎样去重。',
])
def test_illustrative_records_cannot_be_made_up_in_personal_interpretation(text):
    from app.services.career_positioning import check_plain_interpretation
    with pytest.raises(ValueError,match='示例'):
        check_plain_interpretation(text,bank=evidence_for_positioning(sources(),''))


def test_instruction_examples_are_not_rejected_as_invented_personal_records():
    from app.services.career_positioning import check_plain_interpretation
    text='请用已有结果说明，例如先说统计对象，再讲怎样处理重复。'
    assert check_plain_interpretation(text,bank=evidence_for_positioning(sources(),''))==text


def test_generation_does_not_require_padding_an_already_answered_turn():
    from app.services.career_exploration_analysis import INSTRUCTION
    assert 'uncertainties通常1项' not in INSTRUCTION
    assert '全部约600字' not in INSTRUCTION
    assert '已回答的问题不再转成新的技术疑问' in INSTRUCTION
    assert 'uncertainties可以为空' in INSTRUCTION
    assert '只有个人解读时不另生成reply、comparisons或question' in INSTRUCTION


def test_native_correction_keeps_fact_rules_without_a_second_json_demo():
    from app.services.career_exploration_analysis import analysis_instruction
    instruction=analysis_instruction([],native_schema=True)
    assert '只输出一个合法JSON对象' not in instruction
    assert '职责只属于明确说过的动作' in instruction
    assert '不编经历、数值、链接' in instruction
    assert 'uncertainties可以为空' in instruction


def test_group_counts_cannot_borrow_an_unselected_duplicate_check_action():
    from app.services.career_positioning import check_plain_interpretation
    bank=[{'id':'resume.project.0','kind':'resume_experience',
           'quote':'我编写 SQL 分组查询统计各活动报名人数'}]
    with pytest.raises(ValueError,match='重复'):
        check_plain_interpretation('按活动统计的报名人数分布是怎样的；哪些活动有重复报名记录。',bank=bank)
    assert check_plain_interpretation('各活动分别有多少人报名。',bank=bank)


def test_later_question_does_not_excuse_an_earlier_invented_method():
    from app.services.career_positioning import check_plain_interpretation
    with pytest.raises(ValueError,match='实现方式'):
        check_plain_interpretation('通过查询统计人数，并人工逐项核对重复报名；尚未说明是否用于展示。',
            bank=evidence_for_positioning(sources(),''))


@pytest.mark.parametrize('text',[
    '可用于判断哪些活动更受欢迎、是否存在报名异常或资源分配是否合理。',
    '说明统计结果，可以让人明白哪个活动更受欢迎。',
])
def test_counting_registrations_does_not_establish_preference_or_resource_quality(text):
    from app.services.career_positioning import check_plain_interpretation
    with pytest.raises(ValueError,match='报名计数不证明'):
        check_plain_interpretation(text,bank=evidence_for_positioning(sources(),''))
    assert check_plain_interpretation('报名人数不代表哪个活动更受欢迎。',bank=evidence_for_positioning(sources(),''))


@pytest.mark.parametrize('text',[
    '如何处理姓名或手机号存在录入差异（如“张三”与“张 三”、“138****1234”与完整号码）的情况？',
    '解释如何定义重复（例如同一姓名和手机号出现在不同活动记录中）。',
])
def test_unread_record_variants_are_not_needed_to_explain_this_experience(text):
    from app.services.career_positioning import check_plain_interpretation
    with pytest.raises(ValueError,match='示例'):
        check_plain_interpretation(text,bank=evidence_for_positioning(sources(),''))


def sources():
    return {'facts': {'skills': ['SQL'], 'projects': [
        {'name': '报名查询', 'role': '负责查询', 'desc': '我写了报名人数的分组查询，逐项核对结果。'}],
        'experiences': [], 'self_report': {}},
        'candidates': [{'role_name': '数据分析', 'skills': ['SQL', '数据解释']} ]}


def candidate():
    return {'role': '数据分析', 'starting_point': '你已经做过分组查询和结果核对，可从解释查询结果开始了解这个方向。',
        'connections': [{'text': '报名查询里有本人查询和核对动作，与目录的 SQL 线索有关。',
                         'evidence_ids': ['resume.project.0', 'direction.0.0']}],
        'uncertainties': [{'kind': 'needs_detail', 'text': '尚不清楚你怎样向别人解释结果。',
                           'evidence_ids': ['resume.project.0']}],
        'next_action': {'kind': 'clarify_experience', 'text': '补充你当时如何解释报名人数的结果。',
                        'reason': '先分清做过但未写清的部分，不必先重做项目。',
                        'evidence_ids': ['resume.project.0']}}


def test_grounded_positioning_keeps_actual_action_and_server_quotes():
    bank = evidence_for_positioning(sources(), '我还没试过把结果讲给别人。')
    result = validate_positioning(candidate(), ['数据分析'], bank)
    assert result['evidence'][0]['quote'] == bank[0]['quote']
    assert '分组查询' in str(result['evidence'])
    assert not {'score', 'level', 'percentile'} & result.keys()


def test_cannot_invent_evidence_or_promote_skill_name_to_project():
    value = candidate()
    value['connections'][0]['evidence_ids'] = ['resume.fake.7']
    with pytest.raises(ValueError):
        validate_positioning(value, ['数据分析'], evidence_for_positioning(sources(), ''))
    skills_only = sources()
    skills_only['facts']['projects'] = []
    bank = evidence_for_positioning(skills_only, '')
    assert not any(e['kind'] == 'resume_experience' for e in bank)
    assert bank[0]['kind'] == 'resume_skill'


def test_missing_resume_does_not_become_admission_of_not_done():
    value = candidate()
    value['uncertainties'][0]['kind'] = 'not_done'
    result = validate_positioning(value, ['数据分析'], evidence_for_positioning(sources(), ''))
    assert result['uncertainties'][0]['kind'] == 'unknown'
    assert '没有记录不等于没有做过' in result['uncertainties'][0]['text']
    value['uncertainties'][0]['evidence_ids'] = ['student.message']
    result = validate_positioning(value, ['数据分析'], evidence_for_positioning(sources(), '我还没试过解释结果'))
    assert result['uncertainties'][0]['kind'] == 'not_done'
    result = validate_positioning(value, ['数据分析'], evidence_for_positioning(sources(), '我想了解下一步'))
    assert result['uncertainties'][0]['kind'] == 'unknown'


@pytest.mark.parametrize('change', ['role', 'extra', 'direction_only'])
def test_rejects_unselected_role_score_and_impersonal_connection(change):
    value = deepcopy(candidate())
    if change == 'role': value['role'] = '前端开发'
    if change == 'extra': value['score'] = 85
    if change == 'direction_only': value['connections'][0]['evidence_ids'] = ['direction.0.0']
    with pytest.raises(ValueError):
        validate_positioning(value, ['数据分析'], evidence_for_positioning(sources(), ''))


def test_experience_projection_preserves_descriptions_and_never_generates_advantages():
    assert experience_items({}) == []
    result = experience_items({'projects': [{'name': '报名查询', 'role': '负责查询', 'desc': '写分组查询'}],
                               'research': [{'name': '读书研究', 'description': '整理三份材料'}]})
    assert result[0]['description'] == '写分组查询'
    assert result[0]['role'] == '负责查询'
    assert result[1]['description'] == '整理三份材料'


def test_model_comparison_rows_for_same_direction_merge_without_losing_content():
    from app.services.career_exploration_analysis import validate_analysis
    value={'reply':'先看本人做过的事。','question':None,'proposal':None,'review':None,
        'comparisons':[{'role':'数据分析','connection':text,'unknown':'仍需了解'+str(i)}
            for i,text in enumerate(['编写查询','核对重复报名','展示结果'])]}
    result=validate_analysis(value,['数据分析'])
    assert len(result['comparisons'])==1
    assert all(text in result['comparisons'][0]['connection'] for text in ['编写查询','核对重复报名','展示结果'])
    assert all('仍需了解'+str(i) in result['comparisons'][0]['unknown'] for i in range(3))
    with pytest.raises(ValueError):
        validate_analysis({**value,'comparisons':[{'role':'另一方向','connection':'假关联','unknown':'?'}]},['数据分析'])


def test_promotion_chain_uses_the_real_neo4j_read_contract(monkeypatch):
    from app.api import career
    class ReadClient:
        def execute_query(self, query, parameters):
            assert parameters == {'role':'数据分析'}
            return [{'chain':['初级数据分析师','数据分析师'],'requirements':['结果解释'],'depth':1}]
    monkeypatch.setattr(career,'get_neo4j_client',lambda:ReadClient())
    assert career._query_promotion_chains('read-only-query',{'role':'数据分析'})[0]['chain']==['初级数据分析师','数据分析师']


@pytest.mark.parametrize('claim',['你已独立完成一次数据任务。','你已做过人工核对。','你已人工执行结果校验。','你已做过端到端轻量任务。','你尚未涉及统计解释。','你只有技能意识但尚未落地操作。'])
def test_reviewed_model_overreach_requires_specific_source_or_correction(claim):
    value=candidate();value['starting_point']=claim
    result=validate_positioning(value,['数据分析'],evidence_for_positioning(sources(),''))
    assert result['reading_status']=='needs_clarification'
    assert result['starting_point'] != claim
    assert '分组查询' in result['starting_point']
    assert result['next_action']==value['next_action']


def test_scope_guard_allows_explicit_action_and_an_open_question():
    value=candidate();value['starting_point']='你已独立完成查询与人工核对。'
    bank=evidence_for_positioning(sources(),'我独立完成查询，也做了人工核对。')
    assert validate_positioning(value,['数据分析'],bank)['starting_point']==value['starting_point']
    value['starting_point']='你已记录查询经历，尚不清楚是否独立完成。'
    assert validate_positioning(value,['数据分析'],evidence_for_positioning(sources(),''))


def test_public_self_report_quote_does_not_dump_internal_metadata():
    source=sources()
    source['facts']['self_report']={'revision':7,'source_confirmation_task_id':'task_private',
                                  'items':{'teamwork':{'level':'强','example':'我整理分工并核对任务完成情况。'}}}
    bank=evidence_for_positioning(source,'')
    value=candidate();value['next_action']['evidence_ids']=['student.self_report']
    public=validate_positioning(value,['数据分析'],bank)
    quote=next(e['quote'] for e in public['evidence'] if e['kind']=='student_statement')
    assert '我整理分工' in quote
    assert 'task_private' not in quote and 'revision' not in quote and 'teamwork' not in quote


def test_partial_interpretation_keeps_text_client_body_consistent_with_grounded_reader():
    from app.services.career_exploration_analysis import validate_analysis
    position=candidate();position['starting_point']='你已独立完成一次端到端任务。'
    value={'reply':'你已独立完成。','question':None,'proposal':None,'review':None,
           'comparisons':[],'positioning':position}
    result=validate_analysis(value,['数据分析'],positioning_evidence=evidence_for_positioning(sources(),''))
    assert result['positioning']['reading_status']=='needs_clarification'
    assert '独立完成' not in result['reply']
    assert result['positioning']['starting_point'] in result['reply']
    assert result['positioning']['next_action']['text'] in result['reply']


def anchored_candidate():
    value = candidate()
    quote = '我写了报名人数的分组查询'
    anchor = {'evidence_id': 'resume.project.0', 'quote': quote}
    value['anchors'] = [anchor]
    value['starting_point'] = '分组查询可以把各活动的报名人数整理出来，是了解数据分析的一项具体任务。'
    for point in [*value['connections'], *value['uncertainties'], value['next_action']]:
        point['anchors'] = [anchor]
        point['evidence_ids'] = ['resume.project.0']
    value['connections'][0]['text'] = '按活动汇总人数，可以回答每个活动有多少人报名。'
    value['uncertainties'][0]['text'] = '当时按哪些字段识别重复报名？'
    value['next_action']['text'] = '说明当时判定重复报名的规则。'
    value['next_action']['reason'] = '先弄清判定规则，才能决定是补清已有查询说明，还是尝试一种新的核对方法。'
    return value


@pytest.mark.parametrize('surface', ['starting_point','connection','uncertainty','action','reason'])
def test_v2_rejects_cross_action_independence_on_every_surface(surface):
    value=anchored_candidate()
    claim='你已独立完成查询和前端展示。'
    if surface=='starting_point': value['starting_point']=claim
    elif surface=='connection': value['connections'][0]['text']=claim
    elif surface=='uncertainty': value['uncertainties'][0]['text']=claim
    elif surface=='action': value['next_action']['text']=claim
    else: value['next_action']['reason']=claim
    bank=evidence_for_positioning(sources(),'查询由我独立编写，前端展示是同伴做的。')
    with pytest.raises(ValueError):
        validate_positioning(value,['数据分析'],bank,require_grounding=True)


def test_v2_facts_are_exact_quotes_not_new_personal_claims():
    value=anchored_candidate();bank=evidence_for_positioning(sources(),'')
    result=validate_positioning(value,['数据分析'],bank,require_grounding=True)
    assert '我写了报名人数的分组查询' in result['starting_point']
    assert result['content_version']==CONTENT_VERSION
    assert '独立' not in result['starting_point']
    value['anchors'][0]['quote']='我独立完成了全部项目'
    with pytest.raises(ValueError,match='原文'):
        validate_positioning(value,['数据分析'],bank,require_grounding=True)


def test_v2_not_done_keeps_only_the_denied_action_not_a_broader_deficit():
    value=anchored_candidate()
    value['uncertainties']=[{'kind':'not_done','text':'你没有沟通能力，也没做过前端。',
        'evidence_ids':['student.message'],
        'anchors':[{'evidence_id':'student.message','quote':'我没有向别人讲解过统计结论'}]}]
    result=validate_positioning(value,['数据分析'],evidence_for_positioning(sources(),'我没有向别人讲解过统计结论，但我做过前端展示。'),require_grounding=True)
    item=result['uncertainties'][0]
    assert item['text']=='你提到：“我没有向别人讲解过统计结论”。'
    assert '没有沟通能力' not in str(result)


def test_v2_unrelated_denial_cannot_license_a_not_done_claim():
    value=anchored_candidate()
    value['uncertainties']=[{'kind':'not_done','text':'没做过查询',
        'evidence_ids':['resume.project.0'],
        'anchors':[{'evidence_id':'resume.project.0','quote':'我写了报名人数的分组查询'}]}]
    with pytest.raises(ValueError,match='否认'):
        validate_positioning(value,['数据分析'],evidence_for_positioning(sources(),'我没有向别人讲解过统计结论'),require_grounding=True)


@pytest.mark.parametrize('text',['这是数据治理的关键分水岭。','这形成了端到端闭环。','你认可SQL的基础价值。'])
def test_v2_action_reasons_cannot_smuggle_jargon_or_motivation(text):
    value=anchored_candidate();value['next_action']['reason']=text
    with pytest.raises(ValueError):
        validate_positioning(value,['数据分析'],evidence_for_positioning(sources(),''),require_grounding=True)


def test_v2_analysis_question_is_checked_and_legacy_draft_not_accepted_for_new_turn():
    from app.services.career_exploration_analysis import validate_analysis
    value={'reply':'无所谓','question':'你已独立完成前端，是否准备申请？','comparisons':[],
        'proposal':None,'review':None,'positioning':anchored_candidate()}
    with pytest.raises(ValueError):
        validate_analysis(value,['数据分析'],positioning_evidence=evidence_for_positioning(sources(),''),require_grounding=True)
    value['question']=None;value['positioning']=candidate()
    with pytest.raises(ValueError,match='原文'):
        validate_analysis(value,['数据分析'],positioning_evidence=evidence_for_positioning(sources(),''),require_grounding=True)


def test_live_prompt_root_contract_includes_anchored_positioning_not_only_an_appendix():
    from app.services.career_exploration_analysis import INSTRUCTION
    root=INSTRUCTION.split('需要澄清时proposal为null')[0]
    assert '"positioning":{' in root and '"anchors":[' in root


def test_fragment_selection_resolves_original_text_and_cannot_switch_record():
    value=anchored_candidate();bank=evidence_for_positioning(sources(),'')
    fragment=next(f for f in bank[0]['fragments'] if f['text']=='我写了报名人数的分组查询')
    anchor={'evidence_id':'resume.project.0','fragment_id':fragment['id']}
    value['anchors']=[anchor]
    for p in [*value['connections'],*value['uncertainties'],value['next_action']]:p['anchors']=[anchor]
    result=validate_positioning(value,['数据分析'],bank,require_grounding=True)
    assert '我写了报名人数的分组查询' in result['starting_point']
    value['anchors']=[{**anchor,'evidence_id':'resume.skill.0'}]
    with pytest.raises(ValueError,match='原文片段ID'):
        validate_positioning(value,['数据分析'],bank,require_grounding=True)


@pytest.mark.parametrize('text',['说一次你写过的SQL查询目标。','这能确认自己已在实践中接触规则。','这能验证是否具备基础的数据表达能力。'])
def test_real_model_presupposition_and_one_practice_certification_are_rejected(text):
    from app.services.career_positioning import check_plain_interpretation
    with pytest.raises(ValueError): check_plain_interpretation(text)


def test_project_name_alone_is_not_a_personal_starting_fact():
    value=anchored_candidate();value['anchors']=[{'evidence_id':'resume.project.0','quote':'报名查询'}]
    with pytest.raises(ValueError,match='项目名|原文'):
        validate_positioning(value,['数据分析'],evidence_for_positioning(sources(),''),require_grounding=True)


@pytest.mark.parametrize('text',['用活动A有87人报名的结果写三句话。','判断实践颗粒度。','统计实际有多少人参与。','判断是否准备好向他人传递分析价值。'])
def test_saved_model_examples_cannot_invent_metrics_or_replace_registration_with_attendance(text):
    from app.services.career_positioning import check_plain_interpretation
    with pytest.raises(ValueError): check_plain_interpretation(text)


def test_partial_team_question_is_not_an_invented_personal_conclusion_or_a_repeat_after_clarification():
    from app.services.career_positioning import unresolved_team_scope
    source=sources();source['facts']['projects']=[{'name':'报名平台','desc':'团队做了前端、后端、数据库和上线。'}]
    unknown='这是团队整体成果，我还没说明本人负责哪部分。'
    bank=evidence_for_positioning(source,unknown)
    result=unresolved_team_scope(['数据分析'],bank)
    assert result['positioning']['reading_status']=='needs_clarification'
    assert result['positioning']['connections']==[] and result['proposal'] is None
    assert '本人具体负责的一件事' in result['positioning']['next_action']['text']
    assert '团队做了前端' in result['reply'] and '你已' not in result['reply']
    clarified=evidence_for_positioning(source,'查询是我写的，页面由同伴做。',[unknown])
    assert unresolved_team_scope(['数据分析'],clarified) is None


def test_interpretation_cannot_guess_manual_or_missing_automation():
    from app.services.career_positioning import check_plain_interpretation
    with pytest.raises(ValueError): check_plain_interpretation('逐项核对是在缺乏自动化去重逻辑时保障结果可信。')


def test_registration_claim_cannot_become_real_participant_count_and_no_quality_premise():
    from app.services.career_positioning import check_plain_interpretation
    bank=evidence_for_positioning(sources(),'')
    for text in ['统计结果代表真实参与人次。','这是在原始数据质量不理想时主动开展的工作。']:
        with pytest.raises(ValueError): check_plain_interpretation(text,bank=bank)
    attendance=evidence_for_positioning({'facts':{'projects':[{'name':'签到核对','desc':'我核对签到记录与实际到场人数。'}]},'candidates':[]},'')
    assert check_plain_interpretation('核对签到可用来统计实际到场人数。',bank=attendance)


def test_duplicate_registration_does_not_establish_cross_activity_policy_or_complexity():
    from app.services.career_positioning import check_plain_interpretation, positioning_output_schema
    bank=evidence_for_positioning(sources(),'')
    for bad in ['这不是简单查表，而是有聚合逻辑的统计任务。',
                '结果能回答当前数据中是否存在同一人报多个活动的情况。']:
        with pytest.raises(ValueError):check_plain_interpretation(bad,bank=bank)
    explicit=evidence_for_positioning(sources(),'我按同一人报多个活动的规则记录重复。')
    assert check_plain_interpretation('按同一人报多个活动记录重复。',bank=explicit)
    import re
    assert not re.search(positioning_output_schema(explicit)['properties']['starting_point']['not']['pattern'],
                         '按同一人报多个活动记录重复。')


def test_current_known_start_cannot_borrow_the_open_question_fragment():
    from app.services.career_positioning import bind_current_reading_questions,positioning_output_schema
    from app.services.resume_improvements import reading_question
    source=sources();source['facts']['projects'][0]['desc']='我编写 SQL 分组查询统计各活动报名人数，逐项核对重复报名；用 Vue 展示查询结果。'
    bank=evidence_for_positioning(source,'')
    known=next(f for f in bank[0]['fragments'] if f['text']=='我编写 SQL 分组查询统计各活动报名人数')
    unclear=next(f for f in bank[0]['fragments'] if f['text']=='逐项核对重复报名')
    reading={'status':'current','resume_interpretation':{'analysis_content_status':'current','analysis':{
        'summary':{'source_quotes':[{'evidence_id':'projects_0','text':known['text']}]},
        'questions':[{**reading_question('rule',unclear['text']),'evidence_ids':['projects_0'],
                      'source_quotes':[{'evidence_id':'projects_0','text':unclear['text']}]}]}}}
    bind_current_reading_questions(bank,reading)
    value={'role':'数据分析','anchors':[{'evidence_id':bank[0]['id'],'fragment_id':unclear['id']}],
        'starting_point':'这一动作能说明是否存在同一人多次登记同一活动的情况。',
        'connections':[],'uncertainties':[],
        'next_action':{'kind':'clarify_experience','choice':'resume-reading-0'}}
    with pytest.raises(ValueError,match='起点|已明确'):
        validate_positioning(value,['数据分析'],bank,require_grounding=True)
    value['anchors'][0]['fragment_id']=known['id']
    value['starting_point']='统计结果能回答每个活动有多少人报名。'
    result=validate_positioning(value,['数据分析'],bank,require_grounding=True)
    assert known['text'] in result['starting_point']
    schema=positioning_output_schema(bank)['properties']['anchors']['items']
    assert schema['properties']['fragment_id']['enum']==[known['id']]


def test_interpretation_cannot_borrow_attendance_from_a_different_project():
    source=sources()
    source['facts']['projects'].append({'name':'另一项签到练习','desc':'我核对签到和实际到场人数。'})
    value=anchored_candidate()
    value['starting_point']='报名统计能说明实际到场人数。'
    with pytest.raises(ValueError,match='报名不等于'):
        validate_positioning(value,['数据分析'],evidence_for_positioning(source,''),require_grounding=True)
