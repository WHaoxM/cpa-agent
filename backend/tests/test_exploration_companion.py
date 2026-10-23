"""Companion contract: real reads, explicit commits, no invented completion."""
import json
import pytest
from backend.tests.test_career_exploration import exploration_store
from backend.tests.test_student_workbench_http_mysql import workbench_isolated_mysql
from backend.tests.test_agent_run_store import store as run_store


def test_internal_turn_binding_failure_rolls_back_run_and_outbox(run_store):
    from sqlalchemy import text
    def reject(conn, run_id):
        assert conn.execute(text('SELECT COUNT(*) FROM agent_run WHERE run_id=:id'), {'id':run_id}).scalar_one()==1
        raise ValueError('business revision conflict')
    with pytest.raises(ValueError, match='revision conflict'):
        run_store.submit(student_id='owner',session_id='session',idempotency_key='companion_atomic_001',
            request_payload={'message':'一起探索','interaction_mode':'agent'},creation_callback=reject)
    with run_store.mysql.engine.connect() as conn:
        assert conn.execute(text('SELECT COUNT(*) FROM agent_run')).scalar_one()==0
        assert conn.execute(text('SELECT COUNT(*) FROM agent_run_dispatch')).scalar_one()==0


def test_scope_rejects_list_and_unbound_sections():
    from app.services.career_exploration_analysis import read_scope,check_read_scope
    with read_scope({('career://me/explorations/exp_owned','facts')}):
        check_read_scope('career://me/explorations/exp_owned','facts','read')
        with pytest.raises(ValueError): check_read_scope('', 'overview', 'list')
        with pytest.raises(ValueError): check_read_scope('career://me/resume', 'projects', 'read')
    check_read_scope('career://me/resume','projects','read')


def test_completion_hook_failure_cannot_publish_business_result(run_store):
    from sqlalchemy import text
    from app.services.agent_runtime.agent_run_store import AgentRunConflict
    row=run_store.submit(student_id='owner',session_id='session',idempotency_key='companion_settle_001',
                         request_payload={'message':'只读探索','interaction_mode':'agent'})['run']
    rid=row['run_id']
    with pytest.raises(AgentRunConflict):
        run_store.record_resource_read(rid,student_id='owner',claim_token=None,label='本次探索',phase='started')
    run_store.persist_plan(rid,student_id='owner',expected_plan_version=0,goal_summary='只读',steps=[{'role':'qa','goal':'读取','skill_ids':[]}])
    run_store.start_step(rid,student_id='owner',plan_version=1,step_index=1)
    run_store.record_resource_read(rid,student_id='owner',claim_token=None,label='本次探索',phase='started')
    run_store.record_resource_read(rid,student_id='owner',claim_token=None,label='本次探索',phase='completed')
    run_store.finish_step(rid,student_id='owner',plan_version=1,step_index=1,outcome='completed',result={'reply':'候选'})
    run_store.begin_finalizing(rid,student_id='owner')
    with run_store.mysql.engine.begin() as conn:
        conn.execute(text('CREATE TABLE companion_settlement (value TEXT)'))
    def reject(conn):
        conn.execute(text("INSERT INTO companion_settlement VALUES ('must rollback')"))
        raise ValueError('source changed before publication')
    with pytest.raises(ValueError,match='source changed'):
        run_store.complete_run(rid,student_id='owner',final_result={'reply':'答复'},completion_callback=reject)
    assert run_store.get_for_owner(rid,student_id='owner')['status']=='finalizing'
    with run_store.mysql.engine.connect() as conn:
        assert conn.execute(text('SELECT COUNT(*) FROM companion_settlement')).scalar_one()==0


def test_scoped_companion_uses_one_typed_protocol_not_generic_blackboard(monkeypatch):
    import importlib
    from langchain_core.messages import AIMessage
    from langchain_core.tools import StructuredTool
    from app.services.agent_runtime.task_packet import TaskPacket
    from app.services.agent_runtime.blackboard import Blackboard
    scoped=importlib.import_module('app.services.agent_runtime.scoped_invoke')
    seen=[]
    def read(value:str)->str: return value
    tool=StructuredTool.from_function(func=read,name='career_data_read',description='read only')
    class Model:
        def invoke(self,messages,**kwargs):
            seen.append((messages,kwargs))
            return AIMessage(content=json.dumps(_proposal(),ensure_ascii=False))
    monkeypatch.setattr(scoped,'build_gated_tools',lambda *_:[tool])
    monkeypatch.setattr('app.services.langgraph_agent._build_llm_with_tools',lambda *_:Model())
    result=scoped.scoped_invoke(TaskPacket(goal='真实资料',subagent='qa',capability_id='career-exploration',read_keys=[],write_keys=[]),Blackboard())
    assert json.loads(result['reply'])['proposal']['selected_role']=='数据分析'
    assert seen[0][1]=={}  # strict auto-parse would reject the existing read tool schemas
    assert '只输出一个合法JSON对象' in seen[0][0][0].content
    assert '完成后用自然语言总结' not in seen[0][0][0].content


def test_read_restriction_survives_tool_executor_thread(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace
    from langchain_core.tools import StructuredTool
    from app.api._helpers import trusted_agent_worker_scope
    from app.services.agent_runtime import gated_tools
    from app.services.agent_runtime.modes import InteractionMode
    from app.services.career_exploration_analysis import read_scope,check_read_scope
    def read(uri:str,section:str)->str:
        check_read_scope(uri,section,'read')
        return '{"success":true,"status":"completed"}'
    monkeypatch.setattr(gated_tools,'pre_tool_use',lambda _n,args,_c:SimpleNamespace(allowed=True,args=args,rewritten=False,verdict=SimpleNamespace(reason='ok')))
    monkeypatch.setattr(gated_tools,'enqueue_audit',lambda **_:None)
    monkeypatch.setattr(gated_tools,'get_artifact_committer',lambda:SimpleNamespace(commit_candidates=lambda *_a,**_k:[]))
    with trusted_agent_worker_scope(run_id='run_'+'b'*32,claim_token='c'*32,
        load_claim_binding=lambda *_:{'student_id':'owner','principal_id':'owner','principal_source':'account','principal_auth_epoch':0},scope_is_current=lambda *_:True):
        with read_scope({('career://me/explorations/owned','facts')}):
            tool=gated_tools._wrap_one(StructuredTool.from_function(func=read,name='career_data_read',description='bound'),
                gated_tools.make_gate_context(interaction_mode=InteractionMode.AGENT,subagent='qa',student_id='owner',session_id='session',plan_tool_allowlist={'career_data_read'},allow_artifact_commit=False),[])
    with ThreadPoolExecutor(max_workers=1) as pool:
        result=pool.submit(tool.invoke,{'uri':'career://me/resume','section':'projects'}).result()
    assert json.loads(result).get('success') is not True


def _prepared(store, message=None):
    from app.services.career_exploration_analysis import AnalysisService
    from app.api._helpers import Principal
    service, mysql, _ = store
    row = service.create('exploration_owner', {'request_id': 'companion_create_001'})
    row = service.patch(row['exploration_id'], 'exploration_owner', {
        'expected_revision': row['revision'], 'compared_roles': ['数据分析', '前端开发']})
    payload = {'expected_revision': row['revision'], 'message': message or '我喜欢查询也想试网页，周末共三小时。'}
    companion = AnalysisService(mysql)
    principal = Principal(principal_id='exploration_owner', student_id='exploration_owner', source='demo')
    turn = companion.submit(row['exploration_id'], 'exploration_owner', payload, 'companion_turn_key_001', principal)
    return companion, row, payload, turn, principal


def _proposal():
    return {'reply': '你可以先用一件小练习比较自己喜欢的部分。', 'question': None,
        'comparisons': [{'role': '数据分析', 'connection': '简历有课程查询经历。', 'unknown': '还不清楚是否喜欢解释结果。'}],
        'proposal': {'selected_role': '数据分析', 'question': '我是否喜欢解释查询结果？', 'weekly_hours': '周末三小时',
            'steps': [{'title': str(i), 'detail': '自己准备五条合成记录，写下查询、结果和感受。'} for i in range(3)]},
        'review': None}


def test_v9_resume_reading_reaches_scoped_consumer_without_promoting_project_scope(exploration_store,monkeypatch):
    from app.services.resume_diagnostic_report import build_resume_diagnostic_report, validate_synthesis, synthesis_facts
    from app.services.job_publication import canonical_json, content_sha256
    from app.services.career_exploration_analysis import run_analysis_step
    from app.services.agent_runtime.task_packet import TaskPacket
    from app.services.agent_runtime.blackboard import Blackboard
    from app.services.agent_runtime.modes import InteractionMode
    from app.api._helpers import trusted_agent_worker_scope
    _,mysql,_=exploration_store
    stored=mysql.fetch_one('SELECT profile_json FROM student_profile WHERE student_id=:sid',{'sid':'exploration_owner'})['profile_json']
    profile=json.loads(stored) if isinstance(stored,str) else stored
    report=build_resume_diagnostic_report(profile,student_id='exploration_owner')
    facts=synthesis_facts(report); fragment=next(f for f in facts[0]['fragments'] if 'SQL' in f['text'])
    point={'text':'查询可以整理已有记录。','evidence_ids':['projects_0'],'anchors':[{'evidence_id':'projects_0','fragment_id':fragment['id']}]}
    report.update(analysis=validate_synthesis({'summary':point,'strengths':[],'questions':[]},facts),analysis_version=9)
    mysql.execute('INSERT INTO resume_diagnostic_report (report_id,student_id,source_confirmation_task_id,status,report_json,content_sha256) VALUES (:rid,:sid,:tid,:status,:p,:sha)',
        {'rid':report['report_id'],'sid':'exploration_owner','tid':report['source_confirmation_task_id'],'status':report['status'],'p':canonical_json(report),'sha':content_sha256(report)},commit=True)
    companion,row,_,turn,_=_prepared(exploration_store)
    from app.services.tools.career_data_read import CareerDataReadTool
    monkeypatch.setattr('app.services.tools.career_data_read.get_mysql_client',lambda:mysql)
    class Repo:
        def record_resource_read(self,*args,**kwargs):pass
        def assert_claim(self,*args):pass
    seen=[]
    def invoke(packet,*args,**kwargs):
        seen.append(packet.goal)
        body=json.loads(packet.goal.split('\n',1)[1])
        assert 'resume_interpretation' in body['projection_scope']['available_interpretations']
        assert '查询可以整理已有记录。' not in packet.goal
        reference=next(r for r in body['source_catalogue'] if r['section']=='facts')
        read=CareerDataReadTool().execute(operation='read',uri=reference['uri'],section='facts')
        assert '查询可以整理已有记录。' in read
        return {'reply':json.dumps(_proposal(),ensure_ascii=False),'skill_versions':[],'tool_calls':[]}
    with trusted_agent_worker_scope(run_id=turn['run_id'],claim_token='a'*32,
        load_claim_binding=lambda *_:{'student_id':'exploration_owner','principal_id':'exploration_owner','principal_source':'account','principal_auth_epoch':0},scope_is_current=lambda *_:True):
        run_analysis_step(mysql=mysql,run_id=turn['run_id'],sid='exploration_owner',repository=Repo(),claim_token='a'*32,invoke=invoke,
            packet=TaskPacket(goal='原简历解读',subagent='qa'),board=Blackboard(),session_id=row['session_id'],mode=InteractionMode.EXPERTS)
    assert len(seen)==1


def test_current_source_question_reaches_real_read_and_persisted_turn(exploration_store):
    from app.services.resume_diagnostic_report import build_resume_diagnostic_report,validate_synthesis,synthesis_facts,_SYNTHESIS_VERSION
    from app.services.job_publication import canonical_json,content_sha256
    from app.services.career_exploration_analysis import run_analysis_step
    from app.services.career_positioning import reading_question_options
    from app.services.agent_runtime.task_packet import TaskPacket
    from app.services.agent_runtime.blackboard import Blackboard
    from app.services.agent_runtime.modes import InteractionMode
    from app.api._helpers import trusted_agent_worker_scope
    _,mysql,_=exploration_store
    stored=mysql.fetch_one('SELECT profile_json FROM student_profile WHERE student_id=:sid',{'sid':'exploration_owner'})['profile_json']
    profile=json.loads(stored) if isinstance(stored,str) else stored
    report=build_resume_diagnostic_report(profile,student_id='exploration_owner')
    facts=synthesis_facts(report);fact=next(f for f in facts if f['id']=='projects_0')
    fragment=next(f for f in fact['fragments'] if 'SQL' in f['text'])
    point={'text':'查询可以整理已有记录。','evidence_ids':[fact['id']],
        'anchors':[{'evidence_id':fact['id'],'fragment_id':fragment['id']}]}
    report.update(analysis=validate_synthesis({'summary':point,'strengths':[],
        'questions':[{'choice':fact['question_choices'][0]['key']}]},facts),analysis_version=_SYNTHESIS_VERSION)
    mysql.execute('INSERT INTO resume_diagnostic_report (report_id,student_id,source_confirmation_task_id,status,report_json,content_sha256) VALUES (:rid,:sid,:tid,:status,:p,:sha)',
        {'rid':report['report_id'],'sid':'exploration_owner','tid':report['source_confirmation_task_id'],
         'status':report['status'],'p':canonical_json(report),'sha':content_sha256(report)},commit=True)
    companion,row,_,turn,_=_prepared(exploration_store,'请先看我的起点，不生成计划。')
    seen=[]
    class Repo:
        def record_resource_read(self,*args,**kwargs):pass
        def assert_claim(self,*args):pass
    def invoke(packet,*args,**kwargs):
        body=json.loads(packet.goal.split('\n',1)[1]);bank=body['positioning_evidence']
        options=reading_question_options(bank);assert len(options)==1
        option=next(iter(options.values()))
        entry=next(e for e in bank if e['id']==option['evidence_id'])
        seen.append(option)
        anchor={'evidence_id':entry['id'],'fragment_id':next(f['id'] for f in entry['fragments'] if 'SQL' in f['text'])}
        candidate={'reply':'','question':None,'comparisons':[],'proposal':None,'review':None,
            'positioning':{'role':'数据分析','anchors':[anchor],'starting_point':'查询可把已有记录整理出来。',
                'connections':[],'uncertainties':[],
                'next_action':{'kind':'clarify_experience','choice':option['key']}}}
        return {'reply':json.dumps(candidate,ensure_ascii=False),'skill_versions':[],'tool_calls':[]}
    with trusted_agent_worker_scope(run_id=turn['run_id'],claim_token='a'*32,
        load_claim_binding=lambda *_:{'student_id':'exploration_owner','principal_id':'exploration_owner','principal_source':'account','principal_auth_epoch':0},scope_is_current=lambda *_:True):
        run_analysis_step(mysql=mysql,run_id=turn['run_id'],sid='exploration_owner',repository=Repo(),claim_token='a'*32,invoke=invoke,
            packet=TaskPacket(goal='当前简历问题',subagent='qa'),board=Blackboard(),session_id=row['session_id'],mode=InteractionMode.EXPERTS)
    saved=mysql.fetch_one('SELECT result_json FROM career_exploration_analysis_turn WHERE turn_id=:tid',{'tid':turn['turn_id']})['result_json']
    saved=json.loads(saved) if isinstance(saved,str) else saved
    assert saved['positioning']['next_action']['text']==report['analysis']['questions'][0]['text']
    assert seen[0]['evidence_id'] in saved['positioning']['next_action']['evidence_ids']
    assert 'choice' not in saved['positioning']['next_action']


def test_turn_outbox_idempotency_conflict_and_owner(exploration_store):
    from app.services.career_exploration import ExplorationConflict
    companion, row, payload, turn, principal = _prepared(exploration_store)
    assert turn['run_status'] == 'queued'
    repeated = companion.submit(row['exploration_id'], 'exploration_owner', payload, 'companion_turn_key_001', principal)
    assert repeated['run_id'] == turn['run_id']
    with pytest.raises(ExplorationConflict):
        companion.submit(row['exploration_id'], 'exploration_owner', {**payload, 'message':'另一件事'}, 'companion_turn_key_001', principal)
    with pytest.raises(LookupError):
        companion.list(row['exploration_id'], 'another_owner')
    assert companion.mysql.fetch_one('SELECT COUNT(*) AS n FROM agent_run_dispatch')['n'] == 1
    assert companion.mysql.fetch_one('SELECT COUNT(*) AS n FROM agent_action_plan')['n'] == 0


@pytest.mark.parametrize('candidate_format',['valid','mixed','invalid_twice'])
def test_analysis_reads_real_resources_then_confirmation_commits_exact_plan(exploration_store,monkeypatch,candidate_format):
    from app.services.career_exploration_analysis import run_analysis_step
    from app.services.agent_runtime.task_packet import TaskPacket
    from app.services.agent_runtime.blackboard import Blackboard
    from app.services.agent_runtime.modes import InteractionMode
    from app.api._helpers import trusted_agent_worker_scope
    from app.services.tools.career_data_read import CareerDataReadTool
    companion, row, payload, turn, _ = _prepared(exploration_store)
    receipts = []
    projections=[]
    def projection(instruction,evidence,**kwargs):
        projections.append(evidence)
        assert '写过 SQL 查询' in json.dumps(evidence,ensure_ascii=False)
        assert evidence['validation_feedback']
        assert 'unverified_candidate' not in evidence
        assert 'role不得重复' in evidence['comparison_contract']
        assert '【positioning结构约束】' in instruction
        limits=json.loads(instruction.split('【positioning结构约束】\n',1)[1].split('\n',1)[0])
        assert limits['positioning.anchors']['maxItems']==2
        if candidate_format=='invalid_twice':
            return {'reply':'不能绕过校验','unexpected':'不完整结果'}
        return _proposal()
    monkeypatch.setattr('app.services.career_exploration.model_json',projection)
    class Repo:
        def record_resource_read(self, _run, **kw): receipts.append(kw)
        def assert_claim(self, *_args): pass
    def invoke(packet, *_args, **kw):
        assert kw['plan_tool_allowlist'] == {'career_data_read'} and not kw['allow_artifact_commit']
        assert '写过 SQL 查询' in packet.goal and '前端开发' in packet.goal
        body = json.loads(packet.goal.split('\n',1)[1])
        assert any(e['kind']=='resume_experience' and '写过 SQL 查询' in json.dumps(e,ensure_ascii=False) for e in body['positioning_evidence'])
        assert body['source_status']=='current'
        assert '未注入' in body['projection_scope']['interpretations']
        denied = json.loads(CareerDataReadTool().execute(operation='read', uri='career://me/resume'))
        assert denied['success'] is False
        return {'reply': json.dumps(_proposal(), ensure_ascii=False) if candidate_format=='valid' else '一个未符合结构的候选解读', 'skill_versions': [], 'tool_calls': []}
    with trusted_agent_worker_scope(run_id=turn['run_id'], claim_token='a'*32,
        load_claim_binding=lambda *_: {'student_id':'exploration_owner', 'principal_id':'exploration_owner',
                                      'principal_source':'account', 'principal_auth_epoch':0}, scope_is_current=lambda *_: True):
        def execute():
            return run_analysis_step(mysql=companion.mysql, run_id=turn['run_id'], sid='exploration_owner',
                repository=Repo(), claim_token='a'*32, invoke=invoke, packet=TaskPacket(goal='探索',subagent='qa'),
                board=Blackboard(), session_id=row['session_id'], mode=InteractionMode.EXPERTS)
        if candidate_format=='invalid_twice':
            with pytest.raises(ValueError):
                execute()
            assert len(projections)==1
            assert companion.mysql.fetch_one('SELECT result_json FROM career_exploration_analysis_turn WHERE turn_id=:tid',{'tid':turn['turn_id']})['result_json'] is None
            assert companion.mysql.fetch_one('SELECT COUNT(*) AS n FROM agent_action_plan')['n']==0
            return
        output=execute()
    assert len(receipts) == 6 and len(output['exploration_analysis']['evidence_summary']) == 3
    assert len(projections)==(candidate_format=='mixed')
    assert companion.list(row['exploration_id'], 'exploration_owner')[0]['result'] is None  # not yet a completed run
    companion.mysql.execute("UPDATE agent_run SET status='completed' WHERE run_id=:run", {'run':turn['run_id']}, commit=True)
    record = companion.domain.get(row['exploration_id'], 'exploration_owner')
    selection = {'selected_role':'数据分析', 'question':_proposal()['proposal']['question'], 'reason':'想看看自己是否喜欢解释结果', 'weekly_hours':'周末三小时'}
    request = {'expected_revision':record['revision'], 'proposal_id':turn['turn_id'], **selection}
    confirmed = companion.domain.start_round(row['exploration_id'], 'exploration_owner', request)
    plan = confirmed['rounds'][0]['plan']
    assert plan['steps'] == _proposal()['proposal']['steps']
    repeated = companion.domain.start_round(row['exploration_id'], 'exploration_owner', request)
    assert repeated['rounds'][0]['plan']['plan_id'] == plan['plan_id']
    assert companion.mysql.fetch_one('SELECT COUNT(*) AS n FROM agent_action_plan')['n'] == 1
    assert confirmed['rounds'][0]['decision'] is None
    assert not exploration_store[2]  # confirmation never calls the old generation model


@pytest.mark.parametrize('changed_draft',[{'compared_roles':['前端开发']},{'interest':'想转向不同的尝试目标'}])
def test_stale_proposal_and_tampered_confirmation_never_write(exploration_store,changed_draft):
    from app.services.career_exploration import ExplorationConflict, encoded
    companion, row, _, turn, _ = _prepared(exploration_store)
    mysql=companion.mysql
    mysql.execute("UPDATE agent_run SET status='completed' WHERE run_id=:run", {'run':turn['run_id']}, commit=True)
    mysql.execute('UPDATE career_exploration_analysis_turn SET result_json=:r WHERE turn_id=:t',
                  {'r':encoded(_proposal()),'t':turn['turn_id']}, commit=True)
    record=companion.domain.get(row['exploration_id'],'exploration_owner')
    request={'proposal_id':turn['turn_id'],'expected_revision':record['revision'], 'reason':'本人愿意尝试',
             **{k:v for k,v in _proposal()['proposal'].items() if k!='steps'}}
    with pytest.raises(ExplorationConflict):
        companion.confirm(row['exploration_id'],'exploration_owner',{**request,'weekly_hours':'十小时'})
    companion.domain.patch(row['exploration_id'],'exploration_owner',{'expected_revision':record['revision'],**changed_draft})
    assert companion.list(row['exploration_id'],'exploration_owner')[0]['stale'] is True
    with pytest.raises(ExplorationConflict):
        companion.confirm(row['exploration_id'],'exploration_owner',{**request,'expected_revision':record['revision']+1})
    assert mysql.fetch_one('SELECT COUNT(*) AS n FROM agent_action_plan')['n']==0


def test_companion_output_rejects_identifiers_and_multiple_questions():
    from app.services.career_exploration_analysis import validate_analysis
    value = {'reply': '先从你做过的事情看起。', 'question': '你更想了解哪一种工作？',
             'comparisons': [], 'proposal': None, 'review': None}
    assert validate_analysis(value, ['数据分析'])['proposal'] is None
    with pytest.raises(ValueError):
        validate_analysis({**value, 'question': ['问题一', '问题二']}, ['数据分析'])
    with pytest.raises(ValueError):
        validate_analysis({**value, 'reply': '读取 career://me/resume'}, ['数据分析'])


def test_existing_trial_review_cannot_offer_a_second_plan():
    from app.services.career_exploration_analysis import validate_analysis
    value=_proposal()
    value['question']='这次想先讨论哪一个困难？'
    value['review']={'observed':'学生提交了查询记录','difficulties':'还未解决重复行','uncertain':'不能判断掌握程度','next_options':'由本人选择'}
    result=validate_analysis(value,['数据分析'],allow_proposal=False)
    assert result['proposal'] is None and result['review']['observed']=='学生提交了查询记录'


def test_clarification_takes_precedence_without_creating_a_plan():
    from app.services.career_exploration_analysis import validate_analysis
    value=_proposal()
    value['question']='你想先验证哪件具体的事？'
    value['proposal']['weekly_hours']=3
    result=validate_analysis(value,['数据分析'])
    assert result['proposal'] is None and result['question']==value['question']
    value['question']=None
    assert validate_analysis(value,['数据分析'])['proposal']['weekly_hours']=='3 小时'


def test_companion_plan_is_a_proposal_not_a_student_decision():
    from app.services.career_exploration_analysis import validate_analysis
    proposal = {'selected_role': '数据分析', 'question': '我是否喜欢解释查询结果？', 'weekly_hours': '三小时',
                'steps': [{'title': str(i), 'detail': '记录自己实际做过的部分。'} for i in range(3)]}
    value = {'reply': '可以先试一件小事。', 'question': None, 'comparisons': [],
             'proposal': proposal, 'review': None}
    assert validate_analysis(value, ['数据分析'])['proposal'] == proposal
    with pytest.raises(ValueError):
        validate_analysis(value, ['前端开发'])
    with pytest.raises(ValueError):
        validate_analysis({**value, 'decision': 'continue'}, ['数据分析'])


def test_direction_filter_projects_only_owner_current_positioning(exploration_store):
    from app.services.career_exploration import encoded
    from app.services.career_positioning import evidence_for_positioning, validate_positioning
    from backend.tests.test_career_positioning import candidate
    companion,row,_,turn,_ = _prepared(exploration_store)
    mysql=companion.mysql
    bank=evidence_for_positioning(companion.domain.get(row['exploration_id'],'exploration_owner')['sources'],'')
    value=_proposal()
    value['positioning']=validate_positioning(candidate(),['数据分析'],bank)
    mysql.execute('UPDATE career_exploration_analysis_turn SET result_json=:r WHERE turn_id=:t',
        {'r':encoded(value),'t':turn['turn_id']},commit=True)
    mysql.execute("UPDATE agent_run SET status='completed' WHERE run_id=:run",{'run':turn['run_id']},commit=True)
    found=companion.domain.list('exploration_owner','数据分析')
    assert found[0]['latest_analysis']['positioning']['role']=='数据分析'
    assert companion.domain.list('another_owner','数据分析')==[]
    assert companion.domain.list('exploration_owner','不在比较中的方向')==[]
    mysql.execute('UPDATE career_insights SET core_skills=:skills WHERE role_name=:role',
        {'role':'数据分析','skills':encoded(['SQL','新的方向线索'])},commit=True)
    expired=companion.domain.list('exploration_owner','数据分析')[0]
    assert expired['latest_analysis']['positioning'] is None
    assert expired['source_status']=='expired'
    assert companion.domain.get(row['exploration_id'],'exploration_owner')['analysis_turns'][0]['result']['positioning']==value['positioning']


@pytest.mark.parametrize('abort',[False, True])
def test_latest_direction_restores_after_pooled_run_submission(exploration_store, abort):
    """An Agent transaction must not move later exploration creation 8h back."""
    from app.services.db.mysql_client import MySQLClient
    from app.services.career_exploration import ExplorationService
    from app.services.agent_runtime.agent_run_store import AgentRunRepository
    _, existing, _ = exploration_store
    mysql=MySQLClient(host=existing.host,port=existing.port,user=existing.user,
        password=existing.password,database=existing.database,pool_size=1,max_overflow=0)
    try:
        mysql.execute("SET time_zone = '+08:00'",commit=True)
        mysql.execute('SET timestamp = 1700000000',commit=True)
        service=ExplorationService(mysql)
        first=service.create('exploration_owner',{'request_id':'restore_local_first'})
        first=service.patch(first['exploration_id'],'exploration_owner',{'expected_revision':first['revision'],'compared_roles':['数据分析']})
        def reject(*_):
            raise ValueError('controlled creation rollback')
        def submit():
            AgentRunRepository(mysql).submit(student_id='exploration_owner',session_id=first['session_id'],
                idempotency_key='restore_first_turn',request_payload={'message':'先了解我的起点。','interaction_mode':'agent'},
                creation_callback=reject if abort else None)
        if abort:
            with pytest.raises(ValueError,match='controlled creation rollback'):
                submit()
        else:
            submit()
        mysql.execute('SET timestamp = 1700000001',commit=True)
        second=service.create('exploration_owner',{'request_id':'restore_later_current'})
        second=service.patch(second['exploration_id'],'exploration_owner',{'expected_revision':second['revision'],'compared_roles':['数据分析']})
        rows=service.list('exploration_owner','数据分析')
        assert rows[0]['exploration_id']==second['exploration_id'], [(r['exploration_id'],str(r['created_at'])) for r in rows]
        assert mysql.fetch_one('SELECT @@session.time_zone AS zone')['zone']=='+08:00'
        assert mysql.fetch_one('SELECT COUNT(*) AS n FROM agent_run')['n']==(0 if abort else 1)
    finally:
        mysql.close()


@pytest.mark.parametrize('operation',['publish','claim_read','binding_read','turn_admission'])
def test_adjacent_agent_paths_do_not_leak_session_clock(exploration_store, operation):
    from app.services.db.mysql_client import MySQLClient
    from app.services.agent_runtime.agent_run_store import AgentRunRepository
    from app.services.agent_runtime.agent_run_dispatch import AgentRunDispatchStore
    from app.services.agent_runtime.turn_idempotency import TurnIdempotencyLedger, install_turn_idempotency_schema
    _, existing, _ = exploration_store
    mysql=MySQLClient(host=existing.host,port=existing.port,user=existing.user,
        password=existing.password,database=existing.database,pool_size=1,max_overflow=0)
    try:
        mysql.execute("SET time_zone = '+08:00'",commit=True)
        repository=AgentRunRepository(mysql)
        run=repository.submit(student_id='exploration_owner',session_id='restore_session',
            idempotency_key='restore_clock_run',request_payload={'message':'读取已保存的起点。','interaction_mode':'agent'})['run']
        dispatch=AgentRunDispatchStore(repository)
        if operation=='publish':
            dispatch.reserve_publish(run['run_id'])
        elif operation=='claim_read':
            assert dispatch.claim_is_current(run['run_id'],'a'*32) is False
        elif operation=='binding_read':
            assert dispatch.get_claim_binding(run['run_id'],'a'*32) is None
        else:
            install_turn_idempotency_schema(mysql)
            TurnIdempotencyLedger(mysql).admit(student_id='exploration_owner',session_id='restore_session',
                key='restore_clock_chat',request_sha256='a'*64)
        assert mysql.fetch_one('SELECT @@session.time_zone AS zone')['zone']=='+08:00'
    finally:
        mysql.close()


def test_old_content_contract_stays_readable_but_not_a_current_summary(exploration_store):
    from app.services.career_exploration import decoded,encoded
    companion,row,_,turn,_=_prepared(exploration_store)
    mysql=companion.mysql
    saved=mysql.fetch_one('SELECT context_json FROM career_exploration_analysis_turn WHERE turn_id=:t',{'t':turn['turn_id']})
    context=decoded(saved['context_json']);context.pop('content_version',None)
    result={'reply':'旧版正文保留','positioning':{'role':'数据分析','starting_point':'旧版起点'}}
    mysql.execute('UPDATE career_exploration_analysis_turn SET context_json=:c,result_json=:r WHERE turn_id=:t',
        {'c':encoded(context),'r':encoded(result),'t':turn['turn_id']},commit=True)
    mysql.execute("UPDATE agent_run SET status='completed' WHERE run_id=:run",{'run':turn['run_id']},commit=True)
    old=companion.list(row['exploration_id'],'exploration_owner')[0]
    assert old['content_status']=='legacy' and old['stale'] is True
    assert old['result']==result
    summary=companion.domain.list('exploration_owner','数据分析')[0]['latest_analysis']
    assert summary['positioning'] is None
