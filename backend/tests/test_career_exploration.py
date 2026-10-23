"""Exploration is a student decision, never a disguised scored job report."""
import pytest
import json
import threading
import requests
from backend.tests.test_student_workbench_http_mysql import workbench_isolated_mysql, _seed_confirmed_resume


@pytest.fixture
def exploration_store(monkeypatch, workbench_isolated_mysql):
    from app.services import career_exploration as module
    mysql, database = workbench_isolated_mysql
    assert database.startswith('cpa_workbench_scenario_')
    mysql.execute('DELETE FROM career_insights', commit=True)
    for role, category, skills in [('数据分析', '数据智能', ['SQL', 'Python']), ('前端开发', '前端', ['Vue', 'TypeScript'])]:
        mysql.execute('INSERT INTO career_insights(role_name,category,core_skills) VALUES (:role,:category,:skills)',
                      {'role': role, 'category': category, 'skills': json.dumps(skills)}, commit=True)
    _seed_confirmed_resume(mysql, student_id='exploration_owner', confirmation_task_id='task_exploration_resume',
                           candidate={'skills': ['SQL'], 'projects': [{'name': '课程查询', 'desc': '写过 SQL 查询'}]})
    observed = []
    def model(instruction, evidence, **kwargs):
        observed.append(evidence)
        if 'steps' in instruction:
            return {'steps': [{'title': '明确一个查询问题', 'detail': '用自选练习数据记录问题和查询结果。'},
                              {'title': '动手整理结果', 'detail': '保存 SQL、结果表及遇到的困难。'},
                              {'title': '记录自己的感受', 'detail': '写下喜欢与不喜欢的部分，作为探索材料而非能力证明。'}]}
        return {'observed': '你提交了练习说明，可讨论实际尝试过的内容。',
                'difficulties': '根据本人记录梳理困难，不推断未提交的成果。',
                'uncertain': '一次练习不能证明胜任岗位。', 'next_options': '是否继续由你自己决定，也可以调整练习或换方向。'}
    monkeypatch.setattr(module, 'model_json', model)
    monkeypatch.setattr(module.ExplorationService, '_session', lambda *args: None)
    return module.ExplorationService(mysql), mysql, observed


def _ready_round(service):
    record = service.create('exploration_owner', {'request_id': 'unique_request_001', 'interest': '数据与网页'})
    record = service.patch(record['exploration_id'], 'exploration_owner', {
        'expected_revision': record['revision'], 'compared_roles': ['数据分析', '前端开发']})
    selection = dict(selected_role='数据分析', question='我是否喜欢解释数据？', reason='课程接触过 SQL', weekly_hours='三小时')
    record = service.start_round(record['exploration_id'], 'exploration_owner', {'expected_revision': record['revision'], **selection})
    return record, selection


def test_full_exploration_reuses_plan_practice_and_keeps_human_decision(exploration_store):
    from app.services.agent_runtime.action_plan_store import ActionPlanStore
    from app.services.agent_runtime.resource_catalog import read_platform_resource
    service, mysql, observed = exploration_store
    before = mysql.fetch_one('SELECT * FROM student_profile WHERE student_id=:sid', {'sid': 'exploration_owner'})
    record, selection = _ready_round(service)
    eid, rid = record['exploration_id'], record['rounds'][0]['round_id']
    repeated = service.start_round(eid, 'exploration_owner', {'expected_revision': 1, **selection})
    assert len(repeated['rounds']) == 1
    record = service.generate_plan(eid, rid, 'exploration_owner')
    pid = record['rounds'][0]['plan']['plan_id']
    assert service.generate_plan(eid, rid, 'exploration_owner')['rounds'][0]['plan']['plan_id'] == pid
    store = ActionPlanStore(mysql)
    plan = store.get_for_owner(pid, student_id='exploration_owner')
    assert plan['source']['kind'] == 'career_exploration' and plan['source']['status'] == 'current'
    step = plan['steps'][0]['step_id']
    store.set_step_completed(pid, step, True, student_id='exploration_owner')
    store.save_step_evidence(pid, step, '我写了两条 SQL，联表卡住了，仍喜欢检查数据是否准确。', None, student_id='exploration_owner')
    practice = read_platform_resource(mysql, student_id='exploration_owner', uri='career://me/action-plans/'+pid, section='evidence')
    step_read = practice['data'][0]['step']
    assert step_read['progress_self_marked'] is True
    assert 'title' not in step_read and 'detail' not in step_read and 'completed' not in step_read
    review_input = {'expected_revision': record['revision'], 'reflection': '实际做了查询，但联表不熟练，愿意继续试。'}
    record = service.generate_review(eid, rid, 'exploration_owner', review_input)
    assert record['rounds'][0]['review']['status'] == 'current'
    assert '联表卡住' in json.dumps(observed[-1], ensure_ascii=False)
    assert 'student_submissions' in observed[-1] and 'task_background_not_execution_evidence' in observed[-1]
    assert '我写了两条 SQL' in record['rounds'][0]['review']['content']['observed']
    assert record['rounds'][0]['decision'] is None
    decision = {'expected_revision': record['revision'], 'choice': 'adjust', 'reason': '先把联表练清楚，再决定是否继续。'}
    record = service.decide(eid, rid, 'exploration_owner', decision)
    assert service.decide(eid, rid, 'exploration_owner', decision)['revision'] == record['revision']
    # Same cycle is read from authoritative records, not transient chat/local state.
    assert service.get(eid, 'exploration_owner')['rounds'][0]['decision']['choice'] == 'adjust'
    next_record = service.start_round(eid, 'exploration_owner', {'expected_revision': record['revision'], **{**selection, 'question': '能否独立解释联表结果？'}})
    assert len(next_record['rounds']) == 2 and next_record['rounds'][0]['plan']['plan_id'] == pid
    resource = read_platform_resource(mysql, student_id='exploration_owner', uri='career://me/explorations/'+eid, section='rounds')
    assert resource['coverage']['total'] == 2
    historical_uri = 'career://me/explorations/'+eid+'/rounds/'+rid
    historical = read_platform_resource(mysql, student_id='exploration_owner', uri=historical_uri, section='overview')
    assert pid in json.dumps(historical, ensure_ascii=False)
    with pytest.raises(LookupError):
        read_platform_resource(mysql, student_id='other_owner', uri=historical_uri, section='overview')
    with pytest.raises(LookupError):
        service.get(eid, 'other_owner')
    assert mysql.fetch_one('SELECT * FROM student_profile WHERE student_id=:sid', {'sid':'exploration_owner'}) == before
    assert mysql.fetch_one('SELECT COUNT(*) AS n FROM career_report')['n'] == 0


def test_practice_changes_invalidate_review_and_source_changes_block_new_plan(exploration_store):
    from app.services.career_exploration import ExplorationConflict
    from app.services.agent_runtime.action_plan_store import ActionPlanStore
    service, mysql, _ = exploration_store
    record, _ = _ready_round(service)
    eid, rid = record['exploration_id'], record['rounds'][0]['round_id']
    record = service.generate_plan(eid, rid, 'exploration_owner')
    plan = ActionPlanStore(mysql).get_for_owner(record['rounds'][0]['plan']['plan_id'], student_id='exploration_owner')
    record = service.generate_review(eid, rid, 'exploration_owner', {'expected_revision':record['revision'], 'reflection':'还没有做完，在准备数据时卡住了。'})
    ActionPlanStore(mysql).save_step_evidence(plan['plan_id'], plan['steps'][0]['step_id'], '后来做了一次查询，结果与预想不同。', None, student_id='exploration_owner')
    assert service.get(eid,'exploration_owner')['rounds'][0]['review']['status'] == 'stale'
    with pytest.raises(ExplorationConflict):
        service.decide(eid,rid,'exploration_owner',{'expected_revision':record['revision'],'choice':'continue','reason':'想继续'})
    profile = json.loads(mysql.fetch_one('SELECT profile_json FROM student_profile WHERE student_id=:sid', {'sid':'exploration_owner'})['profile_json'])
    profile['skills'].append('Python')
    mysql.execute('UPDATE student_profile SET profile_json=:profile WHERE student_id=:sid', {'profile':json.dumps(profile),'sid':'exploration_owner'},commit=True)
    assert service.get(eid,'exploration_owner')['rounds'][0]['source_status'] == 'unavailable'
    mysql.execute('DELETE FROM student_profile WHERE student_id=:sid', {'sid':'exploration_owner'}, commit=True)
    _seed_confirmed_resume(mysql, student_id='exploration_owner', confirmation_task_id='task_updated_resume',
                           candidate={'skills':['SQL','Python'], 'projects':[{'name':'新项目','desc':'实际新确认的资料'}]})
    assert service.get(eid,'exploration_owner')['rounds'][0]['source_status'] == 'expired'
    assert service.get(eid,'exploration_owner')['rounds'][0]['plan']['plan_id'] == plan['plan_id']


def test_exploration_http_owner_strict_inputs_conflict_and_no_fake_success(monkeypatch, exploration_store):
    from flask import Flask, g
    from werkzeug.serving import make_server
    from app.api import career_bp
    from app.config import Config
    service, mysql, observed = exploration_store
    monkeypatch.setattr(Config,'DEMO_MODE',False)
    app=Flask(__name__);app.register_blueprint(career_bp,url_prefix='/api/career')
    identity={'student_id':'exploration_owner'}
    @app.before_request
    def bind():
        if identity.get('student_id'):
            g.cpa_principal={'principal_id':identity['student_id'],'student_id':identity['student_id']}
    server=make_server('127.0.0.1',0,app,threaded=True)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}/api/career/explorations'
    try:
        assert requests.post(base,json={'request_id':'http_request_001','student_id':'other'}).status_code == 400
        response=requests.post(base,json={'request_id':'http_request_001','interest':'数据'})
        assert response.status_code == 200,response.text
        record=response.json()['data'];url=base+'/'+record['exploration_id']
        assert requests.patch(url,json={'expected_revision':0,'compared_roles':['数据分析']}).status_code == 409
        assert requests.get(url+'?student_id=other').status_code == 400
        identity['student_id']='other'
        assert requests.get(url).status_code == 404
        assert requests.get(base).json()['data'] == []
        identity.clear()
        assert requests.get(url).status_code == 401
        assert mysql.fetch_one('SELECT COUNT(*) AS n FROM career_exploration')['n'] == 1
        assert not observed
    finally:
        server.shutdown();server.server_close();thread.join(5)


def test_exploration_inputs_cannot_claim_an_owner_or_report():
    from app.services.career_exploration import validate_selection
    valid = dict(selected_role='数据分析', question='我是否喜欢整理数据并解释结果？',
                 reason='课程项目接触过 SQL，想试一次', weekly_hours='3 小时')
    assert validate_selection(valid, ['数据分析'])['selected_role'] == '数据分析'
    for changes in ({'student_id': 'other'}, {'report_id': 'forged'}, {'selected_role': '不存在'},
                    {'weekly_hours': ''}):
        with pytest.raises(ValueError):
            validate_selection({**valid, **changes}, ['数据分析'])


def test_exploration_model_shape_is_bounded_and_cannot_publish_links():
    from app.services.career_exploration import validate_steps, validate_review
    steps = [{'title': '整理样本', 'detail': '用本人练习数据记录处理过程与产出。'}] * 3
    assert len(validate_steps({'steps': steps})) == 3
    with pytest.raises(ValueError):
        validate_steps({'steps': [{**steps[0], 'detail': '打开 https://example.test/course'}] * 3})
    with pytest.raises(ValueError):
        validate_steps({'steps': steps * 3})
    with pytest.raises(ValueError):
        validate_review({'decision': 'continue'})


def test_catalogue_projection_never_exposes_market_numbers():
    from app.services.career_exploration import project_candidate
    result = project_candidate({'role_name': '数据分析', 'category': '数据',
                                'core_skills': '["SQL", "Python"]', 'avg_salary_low': 20}, ['SQL'])
    assert result['matched_skills'] == ['SQL']
    assert result['source_status'] == 'unverified_catalogue'
    assert 'avg_salary_low' not in result and 'score' not in result
    assert project_candidate({'role_name': 'æœºå™¨å­¦ä¹ å·¥ç¨‹å¸ˆ'}, []) is None


def test_unplanned_stale_round_can_be_explicitly_reprepared_without_fake_decision(exploration_store):
    from app.services.career_exploration import ExplorationConflict
    service, mysql, _ = exploration_store
    record, selection = _ready_round(service)
    eid, rid = record['exploration_id'], record['rounds'][0]['round_id']
    mysql.execute("UPDATE career_insights SET core_skills='[\"SQL\",\"统计\"]' WHERE role_name='数据分析'", commit=True)
    with pytest.raises(ExplorationConflict):
        service.generate_plan(eid, rid, 'exploration_owner')
    with pytest.raises(LookupError):
        service.restart_unplanned(eid, rid, 'other', {'expected_revision': record['revision']})
    record = service.restart_unplanned(eid, rid, 'exploration_owner', {'expected_revision': record['revision']})
    assert record['rounds'][0]['closed_reason'] == 'reprepared_before_plan'
    assert record['rounds'][0]['decision'] is None and record['rounds'][0]['plan'] is None
    with pytest.raises(ExplorationConflict):
        service.generate_plan(eid, rid, 'exploration_owner')
    record = service.patch(eid, 'exploration_owner', {'expected_revision': record['revision'], 'refresh_sources': True})
    record = service.start_round(eid, 'exploration_owner', {'expected_revision': record['revision'], **selection})
    assert len(record['rounds']) == 2 and record['rounds'][0]['selection'] == selection
    current = record['rounds'][1]['round_id']
    record = service.generate_plan(eid, current, 'exploration_owner')
    with pytest.raises(ExplorationConflict):
        service.restart_unplanned(eid, current, 'exploration_owner', {'expected_revision': record['revision']})


def test_failed_model_preserves_round_and_cas_draft_does_not_overwrite(exploration_store, monkeypatch):
    from app.services import career_exploration as module
    service, mysql, _ = exploration_store
    record, _ = _ready_round(service)
    eid, rid = record['exploration_id'], record['rounds'][0]['round_id']
    service.patch(eid, 'exploration_owner', {'expected_revision': record['revision'], 'interest': '本页保存'})
    with pytest.raises(module.ExplorationConflict):
        service.patch(eid, 'exploration_owner', {'expected_revision': record['revision'], 'interest': '迟到的旧页'})
    def unavailable(*args, **kwargs):
        raise RuntimeError('controlled model failure')
    monkeypatch.setattr(module, 'model_json', unavailable)
    with pytest.raises(RuntimeError):
        service.generate_plan(eid, rid, 'exploration_owner')
    current = service.get(eid, 'exploration_owner')
    assert current['draft']['interest'] == '本页保存'
    assert current['rounds'][0]['selection'] == record['rounds'][0]['selection']
    assert current['rounds'][0]['plan'] is None
    assert mysql.fetch_one('SELECT COUNT(*) AS n FROM agent_action_plan')['n'] == 0


def test_concurrent_generation_commits_one_round_plan(exploration_store, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from app.services import career_exploration as module
    service, mysql, _ = exploration_store
    record, _ = _ready_round(service)
    eid, rid = record['exploration_id'], record['rounds'][0]['round_id']
    original = module.model_json
    barrier = threading.Barrier(2)
    def simultaneous(*args, **kwargs):
        barrier.wait(timeout=15)
        return original(*args, **kwargs)
    monkeypatch.setattr(module, 'model_json', simultaneous)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: service.generate_plan(eid, rid, 'exploration_owner'), range(2)))
    assert responses[0]['rounds'][0]['plan']['plan_id'] == responses[1]['rounds'][0]['plan']['plan_id']
    assert mysql.fetch_one('SELECT COUNT(*) AS n FROM agent_action_plan')['n'] == 1
    assert mysql.fetch_one('SELECT COUNT(*) AS n FROM agent_artifact')['n'] == 1


def test_truncated_or_oversized_generation_is_not_a_complete_result(monkeypatch):
    from types import SimpleNamespace
    from app.services.career_exploration import model_json
    from app.utils import llm_client
    def truncated(**kwargs):
        return SimpleNamespace(text='{"steps":[]}', finish_reason='length', context_status=SimpleNamespace(fits=True))
    monkeypatch.setattr(llm_client, 'get_llm_client', lambda: SimpleNamespace(chat_with_metadata=truncated))
    with pytest.raises(ValueError, match='不完整'):
        model_json('生成练习', {}, max_tokens=50)
    with pytest.raises(ValueError, match='超过读取范围'):
        model_json('生成练习', {'text': '长'*18001}, max_tokens=50)
