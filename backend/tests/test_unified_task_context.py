"""Task identity, atomic admission and budget classification; no real model."""
from types import SimpleNamespace
import pytest
from backend.tests.test_student_workbench_http_mysql import workbench_isolated_mysql


@pytest.fixture
def task_store(workbench_isolated_mysql, monkeypatch):
    from app.services.agent_runtime.task_context import TaskContextStore
    from app.services.agent_runtime.media import file_store
    mysql, _ = workbench_isolated_mysql
    mysql.execute("INSERT INTO conversation(session_id,student_id,status,message_count,created_at,updated_at) VALUES ('session_task','owner','active',0,NOW(),NOW())", commit=True)
    def load(fid): return {
        'file_id': fid, 'student_id': 'owner' if fid == 'file_owned' else 'other',
        'content_sha256': 'original', 'filename': 'resume.docx', 'status': 'ready'}
    monkeypatch.setattr(file_store, 'get_agent_file_service', lambda: SimpleNamespace(get=load,_load_file=load))
    return TaskContextStore(mysql), SimpleNamespace(session_id='session_task', incarnation_id='generation1')


def test_handoff_owner_version_and_idempotency(task_store):
    from app.services.agent_runtime.task_context import TaskContextConflict
    store, session = task_store
    request = {'refs': [{'file_id': 'file_owned'}], 'expected_revision': 0}
    first = store.handoff(session, 'owner', request, 'handoff-one')
    assert first['revision'] == 1
    assert store.handoff(session, 'owner', request, 'handoff-one') == first
    with pytest.raises(TaskContextConflict): store.handoff(session, 'owner', request, 'handoff-two')
    with pytest.raises(PermissionError): store.handoff(session, 'owner', {'refs': [{'file_id': 'file_other'}], 'expected_revision': 1}, 'handoff-bad')
    with pytest.raises(PermissionError): store.read(session.session_id, 'other')
    with pytest.raises(TaskContextConflict): store.read(session.session_id, 'owner', 'generation2')
    assert store.read(session.session_id, 'owner')['revision'] == 1


def test_unsent_attachment_survives_reload_and_is_consumed_only_on_admission(task_store):
    """Reload reads the DB, not a previous client store or the latest user file."""
    from app.services.agent_runtime.task_context import TaskContextStore
    store, session = task_store
    payload = {'refs': [{'file_id': 'file_owned'}], 'expected_revision': 0,
               'purpose': 'composer'}
    store.handoff(session, 'owner', payload, 'composer-upload')
    restored = TaskContextStore(store.mysql).read(session.session_id, 'owner')
    assert [r['file_id'] for r in restored['composer_files']] == ['file_owned']
    assert not restored.get('active_run_id')
    assert not restored.get('focus')  # Upload is not analysis or a formal confirmation.
    with store.mysql.engine.begin() as conn:
        store.bind_run(conn, session, 'owner', restored['revision'], 'run_new_file')
    admitted = store.read(session.session_id, 'owner')
    assert admitted['composer_files'] == []
    assert admitted['focus'][0]['file_id'] == 'file_owned'
    assert admitted['focus'][0]['version'] == 'original'


def test_composer_remove_is_persistent_and_cannot_steal_attachments(task_store):
    from app.services.agent_runtime.task_context import TaskContextConflict
    store, session = task_store
    store.handoff(session, 'owner', {'refs': [{'file_id': 'file_owned'}],
        'expected_revision': 0, 'purpose': 'composer'}, 'composer-add')
    with pytest.raises(PermissionError):
        store.handoff(session, 'owner', {'refs': [{'file_id': 'file_other'}],
            'expected_revision': 1, 'purpose': 'composer'}, 'composer-other')
    with pytest.raises(TaskContextConflict):
        store.handoff(session, 'owner', {'refs': [], 'expected_revision': 0,
            'purpose': 'composer'}, 'composer-stale')
    assert len(store.read(session.session_id, 'owner')['composer_files']) == 1
    store.handoff(session, 'owner', {'refs': [], 'expected_revision': 1,
        'purpose': 'composer'}, 'composer-remove')
    assert store.read(session.session_id, 'owner')['composer_files'] == []


@pytest.mark.parametrize('status,version', [('queued', 'original'), ('failed', 'original'), ('ready', 'changed')])
def test_pending_attachment_never_falls_back_to_old_resume(task_store, monkeypatch, status, version):
    from app.services.agent_runtime.task_context import TaskContextConflict
    from app.services.agent_runtime.media import file_store
    store, session = task_store
    store.handoff(session, 'owner', {'refs': [{'file_id': 'file_owned'}],
        'expected_revision': 0, 'purpose': 'composer'}, 'composer-add')
    changed = {'file_id': 'file_owned', 'student_id': 'owner', 'status': status,
               'content_sha256': version, 'filename': 'resume.docx'}
    monkeypatch.setattr(file_store, 'get_agent_file_service', lambda: SimpleNamespace(
        get=lambda _: changed, _load_file=lambda _: changed))
    with pytest.raises(TaskContextConflict):
        with store.mysql.engine.begin() as conn:
            store.bind_run(conn, session, 'owner', 1, 'must_not_run')
    actual = store.read(session.session_id, 'owner')
    assert actual['revision'] == 1 and actual['composer_files']
    assert not actual.get('active_run_id')


def test_upload_response_commits_selection_before_acknowledgement(task_store, monkeypatch):
    import io
    from flask import Flask
    from app.api import agent_media
    from app.services import session_manager, db
    from app.services.agent_runtime.media import file_store
    store, session = task_store
    session.student_id = 'owner'
    manager = SimpleNamespace(get_session=lambda _: session, refresh_session=lambda *a, **k: session)
    monkeypatch.setattr(session_manager, 'get_session_manager', lambda: manager)
    monkeypatch.setattr(agent_media, 'resolve_request_scope', lambda **k: SimpleNamespace(student_id='owner'))
    monkeypatch.setattr(agent_media, 'require_owner', lambda actual, scope, **k: actual == scope.student_id or (_ for _ in ()).throw(PermissionError()))
    monkeypatch.setattr(db, 'get_mysql_client', lambda: store.mysql)
    uploaded = []
    def upload(**kwargs):
        uploaded.append(kwargs['student_id'])
        return {'file_id': 'file_owned', 'filename': 'resume.docx', 'status': 'ready'}
    file_service = file_store.get_agent_file_service()
    monkeypatch.setattr(file_store, 'get_agent_file_service', lambda: SimpleNamespace(
        upload=upload, get=file_service.get, _load_file=file_service._load_file))
    app = Flask(__name__)
    app.add_url_rule('/files', view_func=agent_media.agent_upload_file, methods=['POST'])
    client = app.test_client()
    def request(version):
        return client.post('/files', data={'session_id': session.session_id,
            'task_context_version': str(version), 'file': (io.BytesIO(b'synthetic'), 'resume.docx')})
    response = request(0)
    assert response.status_code == 200
    assert response.get_json()['data']['task_context']['composer_files'][0]['file_id'] == 'file_owned'
    assert store.read(session.session_id, 'owner')['composer_files'][0]['file_id'] == 'file_owned'
    # Concurrent stale upload is refused BEFORE file persistence, not a false success.
    assert request(0).status_code == 409
    assert uploaded == ['owner']


def test_task_result_is_not_visible_before_completion_commit(task_store):
    from app.services.agent_runtime.task_context import TaskContextConflict
    store, session = task_store
    with store.mysql.engine.begin() as conn:
        store.bind_run(conn, session, 'owner', 0, 'run_bound')
    store.stage(session.session_id, 'owner', 'run_bound', {'reply': '候选答复'})
    assert 'latest_result' not in store.read(session.session_id, 'owner')
    with pytest.raises(RuntimeError):
        with store.mysql.engine.begin() as conn:
            store.settle(conn, session.session_id, 'owner', 'run_bound')
            raise RuntimeError('simulate run completion rollback')
    assert 'latest_result' not in store.read(session.session_id, 'owner')
    with store.mysql.engine.begin() as conn:
        store.settle(conn, session.session_id, 'owner', 'run_bound')
    assert store.read(session.session_id, 'owner')['latest_result']['reply'] == '候选答复'
    with pytest.raises(TaskContextConflict): store.stage(session.session_id, 'owner', 'run_old', {})


def test_usage_unknown_is_not_presented_as_exhausted_budget():
    from app.services.agent_runtime.budget import budget_stop_reply
    assert '不表示' in budget_stop_reply('turn_token_usage_unverified')
    assert '额度不足' in budget_stop_reply('turn_token_cap_exceeded')
    assert '窗口' in budget_stop_reply('estimated_input_over_window')


@pytest.mark.parametrize('capability,allowed',[('student-task', True), ('career-planning', False)])
def test_durable_attachment_support_is_scoped_to_the_new_task_adapter(capability, allowed):
    from app.services.agent_runtime.agent_run_executor import AgentRunExecutor
    failed=[]
    repository=SimpleNamespace(get_for_owner=lambda *a,**k:{'status':'queued','current_plan_version':0},
        get_input_snapshot_for_owner=lambda *a,**k:{'message':'分析简历','interaction_mode':'experts',
            'expert_id':'task-coordinator' if allowed else 'career-coach','capability_id':capability,'attachment_ids':['file_owned']},
        fail_run=lambda *a,**k:failed.append(k))
    dispatch=SimpleNamespace(claim_is_current=lambda *a:True,get_claim_binding=lambda *a:{'student_id':'owner'})
    executor=AgentRunExecutor(repository,dispatch)
    executor._renew_or_stop=lambda *a:None
    executor._has_current_account=lambda *a:True
    planned=[]
    executor._plan=lambda *a,**k:planned.append(True) or True
    executor._advance_once('run_fixture','owner','claim')
    assert bool(planned) is allowed
    assert bool(failed) is not allowed


def test_resume_adapter_keeps_parent_budget_and_continues_versioned_real_windows(monkeypatch):
    from app.services.agent_runtime.student_task import _resume_read
    from app.services.agent_runtime.media import file_store,document_access,session_resources
    from app.services.agent_runtime.budget import capture_usage_ledger,current_usage_ledger
    from app.services.agent_runtime.blackboard import Blackboard
    body='开头标记。'+('课程练习。'*2000)+'文末没有实习。'
    meta={'file_id':'file_owned','student_id':'owner','status':'ready','filename':'resume.docx','content_sha256':'immutable'}
    # Real get() is deliberately a public projection: never contains disk paths.
    public={k:v for k,v in meta.items() if k not in {'storage_path','content_sha256'}}
    monkeypatch.setattr(file_store,'get_agent_file_service',lambda:SimpleNamespace(get=lambda _id:public,_load_file=lambda _id:meta))
    monkeypatch.setattr(document_access,'_full_text',lambda _id:(body,{'filename':'resume.docx'}))
    monkeypatch.setattr(session_resources,'mount_resources',lambda *a,**k:None)
    receipts=[];repo=SimpleNamespace(record_resource_read=lambda *a,**k:receipts.append(k))
    calls=[]
    with capture_usage_ledger('root',turn_token_cap=30000) as ledger:
        def invoke(packet,board,session,**kwargs):
            assert current_usage_ledger() is ledger
            assert kwargs['allow_tools'] is False
            assert 'file_owned' not in packet.goal
            calls.append(packet.goal)
            return {'reply':'本次仅依据已读取片段分析','skill_versions':[]}
        _,first=_resume_read(None,['file_owned'],'owner','session',{},'分析简历','run',repo,None,invoke,Blackboard())
        assert first['continuation'][0]['next_offset']==4000
        _,second=_resume_read(None,['file_owned'],'owner','session',first,'继续读取','run',repo,None,invoke,Blackboard())
        assert second['continuation'][0]['next_offset']==8000
        assert '开头标记' in calls[0] and '开头标记' not in calls[1]
        assert all(r['status']=='partial' for r in second['evidence_summary'])
        assert all(r['provenance']=='student_submitted_unverified' for r in second['evidence_summary'])
        # Discussion does not consume the next page; EOF never loops to page 1.
        _, discussion=_resume_read(None,['file_owned'],'owner','session',first,'这段能写进简历吗','run',repo,None,invoke,Blackboard(),read_action='discuss')
        assert discussion['document_reads'][0]['window_start']==0
        assert discussion['continuation'][0]['next_offset']==4000
        _, tail=_resume_read(None,['file_owned'],'owner','session',second,'剩下还有什么','run',repo,None,invoke,Blackboard())
        assert tail['document_reads'][0]['complete'] is True
        _, eof=_resume_read(None,['file_owned'],'owner','session',tail,'再往后读','run',repo,None,invoke,Blackboard())
        assert eof['document_reads']==tail['document_reads']
        assert '开头标记' not in calls[-1]
        def unsafe(*args,**kwargs):return {'reply':'已确认的实践经历：SQL 练习。'}
        with pytest.raises(ValueError,match='unconfirmed_resume_claim'):
            _resume_read(None,['file_owned'],'owner','session',first,'继续','run',repo,None,unsafe,Blackboard())
        from app.services.agent_runtime.task_context import TaskContextConflict
        monkeypatch.setattr(document_access,'_full_text',lambda _id:('changed',{}))
        with pytest.raises(TaskContextConflict):_resume_read(None,['file_owned'],'owner','session',first,'继续','run',repo,None,invoke,Blackboard())
    assert len(calls)==5
    assert [r['phase'] for r in receipts[:4]]==['started','completed','started','completed']


def test_task_lead_has_one_protocol_and_rejects_an_unlisted_direction(monkeypatch):
    from app.services.agent_runtime import experts_lead
    captured=[]
    def chat(**kwargs):
        prompt=kwargs['messages'][0]['content']
        assert '唯一输出协议' in prompt
        assert 'user_job:' not in prompt
        assert 'capability_fit' not in prompt
        assert 'steps 是顺序执行' not in prompt
        captured.append(prompt)
        return '{"agent":"career_exploration","goal_summary":"比较","reply":"","compared_roles":["数据分析"]}'
    monkeypatch.setattr('app.utils.llm_client.get_llm_client',lambda:SimpleNamespace(chat=chat))
    result=experts_lead.task_lead_decide('想比较两个方向',{'catalogue':['数据分析']})
    assert result['agent']=='career_exploration'
    rejected=experts_lead.task_lead_decide('比较',{'catalogue':[]})
    assert rejected['agent']=='clarify'
    assert '当前可用目录' in rejected['reply']
    assert len(captured)==2


def test_task_lead_accepts_same_session_conversation_recall(monkeypatch):
    from app.services.agent_runtime import experts_lead

    captured = []

    def chat(**kwargs):
        captured.extend(kwargs['messages'])
        return ('{"agent":"conversation_recall","goal_summary":"回顾本会话内容",'
                '"reply":"","compared_roles":[],"document_action":"discuss"}')

    monkeypatch.setattr('app.utils.llm_client.get_llm_client', lambda: SimpleNamespace(chat=chat))
    result = experts_lead.task_lead_decide(
        '我刚才提到的编号是什么？',
        {'catalogue': [], 'recent_discussion': {'messages': [
            {'role': 'user', 'content': '编号是 ZXQ-421。'}]}},
    )

    assert result['agent'] == 'conversation_recall'
    assert 'conversation_recall' in captured[0]['content']


@pytest.mark.parametrize('raw,reply_fragment', [
    ({'agent': 'practice_review', 'compared_roles': []}, '没有独立的实践记录复盘流程'),
    ({'agent': 'send_email', 'compared_roles': []}, '没能可靠判断'),
    ({'agent': ['career_exploration'], 'compared_roles': []}, '没能可靠判断'),
    ({'agent': 'career_exploration', 'compared_roles': '数据分析'}, '当前可用目录'),
    ({'agent': 'career_exploration', 'compared_roles': [42]}, '当前可用目录'),
    ({'agent': 'career_exploration', 'compared_roles': ['数据分析', '数据分析']}, '当前可用目录'),
    ({'agent': 'career_exploration', 'compared_roles': ['未发布方向']}, '当前可用目录'),
    ({'agent': 'resume_preparation', 'compared_roles': [], 'document_action': 'read_everything'}, '没能可靠判断'),
])
def test_task_lead_unsupported_or_malformed_intent_stops_at_clarify(
    monkeypatch, raw, reply_fragment,
):
    import json
    from app.services.agent_runtime import experts_lead

    monkeypatch.setattr(
        'app.utils.llm_client.get_llm_client',
        lambda: SimpleNamespace(chat=lambda **_kwargs: json.dumps(raw, ensure_ascii=False)),
    )

    result = experts_lead.task_lead_decide(
        '复盘我的实践记录', {'catalogue': ['数据分析']},
    )

    assert result['agent'] == 'clarify'
    assert result['goal_summary'] == '当前请求尚不能安全映射到已实现的任务路径。'
    assert result['compared_roles'] == []
    assert result['document_action'] == 'discuss'
    assert reply_fragment in result['reply']


def test_task_lead_prompt_sets_no_read_correction_and_mixed_intent_boundaries(monkeypatch):
    from app.services.agent_runtime import experts_lead
    captured = []

    def chat(**kwargs):
        captured.append(kwargs['messages'][0]['content'])
        return ('{"agent":"clarify","goal_summary":"","reply":"请说明优先项",'
                '"compared_roles":[],"document_action":"discuss"}')

    monkeypatch.setattr('app.utils.llm_client.get_llm_client', lambda: SimpleNamespace(chat=chat))
    result = experts_lead.task_lead_decide(
        '不要读取简历；纠正一下，并先帮我比较方向，再讨论实践记录。',
        {'catalogue': ['数据分析'], 'recent_discussion': {'messages': []}},
    )

    assert result['agent'] == 'clarify'
    prompt = captured[0]
    assert 'practice_review' not in prompt
    assert '没有独立的实践记录复盘路径' in prompt
    assert '不读取/不访问资料' in prompt
    assert '以当前用户原话为准' in prompt
    assert '没有明确顺序或优先级' in prompt


def test_student_task_history_reconciles_same_session_rows_by_message_id(monkeypatch):
    from app.services.agent_runtime import student_task
    from app.services import session_manager

    redis_messages = [
        {'message_id': 'msg-2', 'role': 'user', 'content': '同一条重复文本'},
        {'message_id': 'msg-3', 'role': 'assistant', 'content': '我会按你刚才的说明处理。',
         'metadata': {'tool_calls': [{'secret': 'must not pass'}]}},
    ]
    manager = SimpleNamespace(
        refresh_session=lambda session_id, require_authoritative: SimpleNamespace(
            student_id='owner', messages=redis_messages,
        ),
    )
    monkeypatch.setattr(session_manager, 'get_session_manager', lambda: manager)

    class Mysql:
        def __init__(self):
            self.sql = ''
            self.params = None

        def fetch_all(self, sql, params):
            self.sql, self.params = sql, params
            # The query is newest-first; the history adapter restores order.
            return [
                {'message_id': 'msg-2', 'role': 'user', 'content': '同一条重复文本'},
                {'message_id': 'msg-1', 'role': 'user', 'content': '同一条重复文本'},
            ]

    mysql = Mysql()
    history = student_task._history(mysql, 'session-a', 'owner')

    assert [item['content'] for item in history] == [
        '同一条重复文本', '同一条重复文本', '我会按你刚才的说明处理。',
    ]
    assert [item['role'] for item in history] == ['user', 'user', 'assistant']
    assert 'JOIN conversation AS c' in mysql.sql
    assert mysql.params == {'session': 'session-a', 'owner': 'owner', 'limit': 32}
    assert 'must not pass' not in str(history)
    assert all(set(item) == {'role', 'content'} for item in history)


def test_student_task_history_does_not_fallback_across_session_owners(monkeypatch):
    from app.services.agent_runtime import student_task
    from app.services import session_manager

    manager = SimpleNamespace(
        refresh_session=lambda session_id, require_authoritative: SimpleNamespace(
            student_id='different-owner', messages=[
                {'message_id': 'private', 'role': 'user', 'content': 'private'}
            ],
        ),
    )
    monkeypatch.setattr(session_manager, 'get_session_manager', lambda: manager)

    class Mysql:
        def fetch_all(self, *_args, **_kwargs):
            raise AssertionError('owner mismatch must stop before SQL fallback')

    assert student_task._history(Mysql(), 'session-a', 'owner') == []


def test_agent_run_recall_keeps_identical_current_text_as_a_distinct_turn():
    from app.services.agent_runtime.scoped_invoke import _ask_context_messages
    from langchain_core.messages import HumanMessage

    history = [{'role': 'user', 'content': '我刚才说的编号是什么？'}]
    messages = _ask_context_messages(
        history, '我刚才说的编号是什么？', append_current_user=True,
    )

    assert len(messages) == 2
    assert all(isinstance(item, HumanMessage) for item in messages)
    assert [item.content for item in messages] == [
        '我刚才说的编号是什么？', '我刚才说的编号是什么？',
    ]


def test_conversation_recall_scoped_adapter_receives_ordered_history_without_tools(monkeypatch):
    from app.services.agent_runtime.blackboard import Blackboard
    from app.services.agent_runtime.modes import InteractionMode
    from app.services.agent_runtime.scoped_invoke import scoped_invoke
    from app.services.agent_runtime.task_packet import build_standard_packet

    captured = {}

    class FakeLlm:
        def chat(self, **kwargs):
            captured.update(kwargs)
            return '你之前问的是 ZXQ-421。'

    monkeypatch.setattr('app.utils.llm_client.get_llm_client', lambda: FakeLlm())
    packet = build_standard_packet(
        'qa', goal='仅按同一会话历史回忆，不调用工具。',
        user_utterance='我刚才问的编号是什么？', read_keys=[], write_keys=[],
        capability_id='student-task-conversation-recall',
    )
    result = scoped_invoke(
        packet, Blackboard(), allow_tools=False,
        interaction_mode=InteractionMode.ASK,
        ask_context_messages=[
            {'role': 'user', 'content': '编号是 ZXQ-421。'},
            {'role': 'assistant', 'content': '已记录。'},
            {'role': 'user', 'content': '我刚才问的编号是什么？'},
        ],
        ask_context_append_current=True,
        ask_context_appendix='仅当前会话；不调用工具或读取附件。',
    )

    sent = captured['messages']
    assert [item['role'] for item in sent] == ['system', 'user', 'assistant', 'user', 'user']
    assert sent[-2]['content'] == '我刚才问的编号是什么？'
    assert '我刚才问的编号是什么？' in sent[-1]['content']
    assert '任务正文：' in sent[-1]['content']
    assert result['reply'] == '你之前问的是 ZXQ-421。'
    assert result['tool_calls'] == []
    assert result['blackboard_updates'] == {}


@pytest.mark.parametrize('history,expected_state', [
    ([], 'needs_input'),
    ([{'role': 'user', 'content': '编号是 ZXQ-421。'}], 'answered_from_conversation'),
])
def test_conversation_recall_uses_only_bounded_history_and_never_tools(
    monkeypatch, history, expected_state,
):
    from app.services.agent_runtime import student_task, experts_lead

    staged = []
    monkeypatch.setattr(student_task, 'TaskContextStore', lambda _mysql: SimpleNamespace(
        read=lambda *_args: {
            'active_run_id': 'run-recall', 'revision': 1, 'focus': [], 'latest_result': {},
            'document_reads': [], 'turns': [],
        },
        stage=lambda *args: staged.append(args[-1]),
    ))
    monkeypatch.setattr(student_task, '_lead_sources', lambda *_args: {'facts': {}, 'candidates': []})
    monkeypatch.setattr(student_task, '_recent', lambda *_args: {
        'messages': history, 'coverage': 'recent_discussion_only',
        'boundary': 'same session only',
    })
    monkeypatch.setattr(experts_lead, 'task_lead_decide', lambda *_args: {
        'agent': 'conversation_recall', 'goal_summary': '回忆本会话', 'reply': '',
        'compared_roles': [], 'document_action': 'discuss',
    })

    calls = []

    def invoke(packet, board, session_id, **kwargs):
        calls.append((packet, session_id, kwargs))
        return {'reply': '你在本会话里提到编号 ZXQ-421。'}

    result = student_task.run_step(
        mysql=None,
        snapshot={'task_context_version': 0, 'message': '我之前提到的编号是什么？'},
        run_id='run-recall', owner='owner', session_id='session-a',
        repository=SimpleNamespace(record_business_delegation=lambda *_args, **_kwargs: None),
        claim_token='claim', invoke=invoke, packet=None, board=object(),
    )

    task = result['student_task']
    assert task['state'] == expected_state
    if history:
        assert len(calls) == 1
        packet, session_id, kwargs = calls[0]
        assert session_id == 'session-a'
        assert packet.capability_id == 'student-task-conversation-recall'
        assert packet.read_keys == [] and packet.write_keys == []
        assert kwargs['allow_tools'] is False
        assert kwargs['ask_context_messages'] == history
        assert kwargs['ask_context_append_current'] is True
        assert task['resource_access'] == 'conversation_only'
    else:
        assert calls == []
        assert '原话再发一次' in task['reply']
    assert len(staged) == 1


def test_unselected_directions_do_not_create_an_exploration_for_a_resume_followup(monkeypatch):
    from app.services.agent_runtime import student_task, experts_lead
    from app.services import career_exploration
    staged=[]
    monkeypatch.setattr(student_task, 'TaskContextStore', lambda _: SimpleNamespace(
        read=lambda *a: {'active_run_id':'run','revision':1,'focus':[{'file_id':'file_owned','name':'新简历','status':'ready'}]},
        stage=lambda *a: staged.append(a[-1])))
    monkeypatch.setattr(experts_lead, 'task_lead_decide', lambda *a: {
        'agent':'career_exploration','compared_roles':[],'reply':'','goal_summary':'核对经历'})
    class Domain:
        def __init__(self, _): pass
        def context(self, _): return {'candidates':[],'facts':{}}
        def create(self, *a): raise AssertionError('No direction selected; must not create a business record')
    monkeypatch.setattr(career_exploration, 'ExplorationService', Domain)
    monkeypatch.setattr(student_task, '_lead_sources', lambda *a: {'candidates':[],'facts':{}})
    monkeypatch.setattr(student_task, '_recent', lambda *a: {})
    result=student_task.run_step(mysql=None,snapshot={'task_context_version':0,'message':'没有担任负责人说明没有项目经历吗'},
        run_id='run',owner='owner',session_id='session',repository=SimpleNamespace(record_business_delegation=lambda *a,**k:None),
        claim_token=None,invoke=None,packet=None,board=None)
    assert result['student_task']['state']=='needs_input'
    assert not result['student_task'].get('exploration_id')
    assert len(staged)==1


def test_unused_exploration_release_does_not_invalidate_resume_but_selected_domain_is_guarded(monkeypatch):
    from app.services.agent_runtime import student_task
    from app.services import career_exploration_analysis
    monkeypatch.setattr(career_exploration_analysis,'release_contract',lambda:{'version':'one'})
    before=student_task.release_contract()
    selected=student_task._exploration_release()
    monkeypatch.setattr(career_exploration_analysis,'release_contract',lambda:{'version':'two'})
    assert student_task.release_contract()==before
    with pytest.raises(RuntimeError,match='runtime_release_changed_during_delegation'):
        student_task._assert_exploration_release(selected)


@pytest.mark.parametrize('reply', ['已确认的实践经历：完成 SQL 练习。', '你的简历内容真实可靠，已经核验。'])
def test_uploaded_resume_reply_cannot_upgrade_student_statement_to_verified_fact(reply):
    from app.services.agent_runtime.student_task import _validate_submitted_reply
    with pytest.raises(ValueError, match='unconfirmed_resume_claim'):
        _validate_submitted_reply(reply)


@pytest.mark.parametrize('reply', ['你提交的材料写了 SQL 练习，尚未经核验。', '这不是已确认的实践经历，不等于能力已经验证。'])
def test_submitted_reply_may_explain_the_confirmation_boundary(reply):
    from app.services.agent_runtime.student_task import _validate_submitted_reply
    assert _validate_submitted_reply(reply)==reply


def test_task_reply_delivers_validated_review_without_sending_student_back_to_page():
    from app.services.agent_runtime.student_task import _discussion_reply
    analysis={'reply':'我读到了两份练习记录。','review':{
        'observed':'本人提交的原始文字，不需要重复一遍',
        'difficulties':'说明统计口径时仍不熟悉。',
        'uncertain':'第三步没有提交，尚不能判断解释效果。',
        'next_options':'可以先用三句话练习解释，由本人选择。'}}
    before=analysis.copy()
    reply=_discussion_reply(analysis)
    assert reply.startswith(analysis['reply'])
    for key in ('difficulties','uncertain','next_options'):
        assert analysis['review'][key] in reply
    assert analysis['review']['observed'] not in reply
    assert analysis==before
    assert _discussion_reply({'reply':'普通方向讨论','review':None})=='普通方向讨论'


@pytest.mark.parametrize('repair',[False,True])
def test_resume_child_receives_earlier_student_corrections_not_assistant_claims(monkeypatch,repair):
    from app.services.agent_runtime import student_task
    from app.services.agent_runtime.media import file_store, document_access, session_resources
    from app.services.agent_runtime.blackboard import Blackboard
    corrections = {'messages':[{'run_id':'old_run','text':'页面是同学写的，我只写SQL；练习数据，不是真实社团；重复行没解决。'}],
                   'coverage':'all_prior_student_messages','boundary':'student_statement_not_verified'}
    monkeypatch.setattr(student_task, '_student_discussion', lambda *a: corrections)
    meta={'file_id':'file_owned','student_id':'owner','status':'ready','filename':'resume.docx'}
    monkeypatch.setattr(file_store,'get_agent_file_service',lambda:SimpleNamespace(get=lambda _:meta))
    monkeypatch.setattr(document_access,'_full_text',lambda _:('原稿写Vue页面与SQL练习',{}))
    monkeypatch.setattr(session_resources,'mount_resources',lambda *a,**k:None)
    goals=[];discussions=[]
    def invoke(packet,*a,**k):
        goals.append(packet.goal)
        discussions.append(packet.student_discussion)
        if repair and len(goals)==1:return {'reply':'接下来我会继续读取后面，请稍等。'}
        return {'reply':'按你补充的分工，只写SQL练习。'}
    _, result=student_task._resume_read(SimpleNamespace(),['file_owned'],'owner','session',{},
        '写成一句话，不要替我揽功','run',SimpleNamespace(record_resource_read=lambda *a,**k:None),None,invoke,Blackboard())
    assert discussions[0]==[corrections['messages'][0]['text']]
    assert corrections['messages'][0]['text'] not in goals[0]  # No duplicate blob; real user-message sequence.
    assert result['discussion_context']['coverage']=='all_prior_student_messages'
    assert result['discussion_context']['answer_validation_retries']==(1 if repair else 0)
    assert len(goals)==(2 if repair else 1)


def test_student_discussion_retains_old_correction_and_refuses_silent_truncation(monkeypatch):
    from app.services.agent_runtime.student_task import _student_discussion
    from app.services.agent_runtime.agent_run_store import AgentRunRepository
    calls=[]
    rows=[{'run_id':f'run_{i}','message':('只有SQL，没有做页面。' if i==0 else f'第{i}次补充。')} for i in range(12)]
    mysql=SimpleNamespace(fetch_all=lambda sql,args:calls.append((sql,args)) or rows)
    monkeypatch.setattr(AgentRunRepository,'_verified_input_snapshot',lambda row:{'message':row['message'],'capability_id':'student-task'})
    value=_student_discussion(mysql,'session','owner')
    assert value['messages'][0]['text']=='只有SQL，没有做页面。'
    assert len(value['messages'])==12
    assert calls[0][1]=={'session':'session','owner':'owner'}
    rows[0]['message']='a'*16001
    assert _student_discussion(mysql,'session','owner')['messages'][0]['text']==rows[0]['message']
    rows.extend({'run_id':f'more_{i}','message':'补充'} for i in range(245))
    with pytest.raises(ValueError,match='task_discussion_capacity'):
        _student_discussion(mysql,'session','owner')


def test_resume_history_reaches_real_model_boundary_as_user_messages(monkeypatch):
    from app.services.agent_runtime.scoped_invoke import scoped_invoke
    from app.services.agent_runtime.task_packet import build_standard_packet
    from app.services.agent_runtime.blackboard import Blackboard
    from app.services.agent_runtime.modes import InteractionMode
    captured=[]
    monkeypatch.setattr('app.utils.llm_client.get_llm_client',lambda:SimpleNamespace(chat=lambda **kw:captured.append(kw['messages']) or '只写本人SQL练习。'))
    packet=build_standard_packet('qa',goal='旧稿提到了页面。',user_utterance='按更正后的分工写一句。',capability_id='student-task-resume',write_keys=[],read_keys=[])
    packet.lean=True
    packet.student_discussion=['页面由同学完成，我只写SQL。','不要再建议新岗位。']
    packet.validate()
    scoped_invoke(packet,Blackboard(),allow_tools=False,interaction_mode=InteractionMode.ASK,student_id='owner',allow_artifact_commit=False)
    assert all(captured[0][i+1]['content'].endswith(text) for i,text in enumerate(packet.student_discussion))
    assert captured[0][-1]['content']=='【本次问题】\n按更正后的分工写一句。'
    assert '旧稿提到了页面' in captured[0][-2]['content']
    assert '纠正优先' in captured[0][0]['content']
    packet.capability_id=None
    with pytest.raises(ValueError):packet.validate()


def test_terminal_read_answer_cannot_fake_a_future_pending_read():
    from app.services.agent_runtime.student_task import _validate_submitted_reply
    with pytest.raises(ValueError,match='task_terminal_answer_pending'):
        _validate_submitted_reply('接下来我会继续读取后面的内容，请稍等。')


def test_document_checkpoint_survives_other_agent_and_failed_commit(task_store):
    store,session=task_store
    checkpoint=[{'file_id':'file_owned','version':'hash','window_start':0,'next_offset':4000,'complete':False}]
    with store.mysql.engine.begin() as conn: store.bind_run(conn,session,'owner',0,'run_read')
    store.stage(session.session_id,'owner','run_read',{'reply':'片段','document_reads':checkpoint})
    with store.mysql.engine.begin() as conn: store.settle(conn,session.session_id,'owner','run_read')
    version=store.read(session.session_id,'owner')['revision']
    with store.mysql.engine.begin() as conn: store.bind_run(conn,session,'owner',version,'run_clarify')
    store.stage(session.session_id,'owner','run_clarify',{'reply':'继续讨论什么？'})
    with store.mysql.engine.begin() as conn: store.settle(conn,session.session_id,'owner','run_clarify')
    assert store.read(session.session_id,'owner')['document_reads']==checkpoint
    version=store.read(session.session_id,'owner')['revision']
    with store.mysql.engine.begin() as conn: store.bind_run(conn,session,'owner',version,'run_rollback')
    store.stage(session.session_id,'owner','run_rollback',{'reply':'下一段','document_reads':[]})
    with pytest.raises(RuntimeError):
        with store.mysql.engine.begin() as conn:
            store.settle(conn,session.session_id,'owner','run_rollback')
            raise RuntimeError('commit failed after budget stop')
    assert store.read(session.session_id,'owner')['document_reads']==checkpoint


@pytest.mark.parametrize('reason',['turn_token_cap_exceeded','turn_token_usage_unverified'])
def test_resume_budget_pause_does_not_consume_the_unanswered_window(monkeypatch, reason):
    from app.services.agent_runtime.student_task import _resume_read
    from app.services.agent_runtime.media import file_store, document_access, session_resources
    from app.services.agent_runtime.blackboard import Blackboard
    from app.services.agent_runtime.budget import capture_usage_ledger, require_turn_token_capacity
    body='第一页。'*1500+'尾页。'*1500
    meta={'file_id':'file_owned','student_id':'owner','status':'ready','filename':'resume.docx'}
    monkeypatch.setattr(file_store,'get_agent_file_service',lambda:SimpleNamespace(get=lambda _:meta))
    monkeypatch.setattr(document_access,'_full_text',lambda _:(body,{}))
    monkeypatch.setattr(session_resources,'mount_resources',lambda *a,**k:None)
    repo=SimpleNamespace(record_resource_read=lambda *a,**k:None)
    ok=lambda *a,**k:{'reply':'只依据本次片段。'}
    _,first=_resume_read(None,['file_owned'],'owner','session',{},'先读前面','run',repo,None,ok,Blackboard())
    import copy
    before=copy.deepcopy(first)
    with capture_usage_ledger('root',turn_token_cap=20) as ledger:
        if reason=='turn_token_usage_unverified':
            ledger['stop_reason']=reason
        def stopped(*a,**k):
            require_turn_token_capacity(30,estimated_input_tokens=10)
        with pytest.raises(RuntimeError,match=reason):
            _resume_read(None,['file_owned'],'owner','session',first,'看看后面的','run',repo,None,stopped,Blackboard())
    assert first==before
    _, resumed=_resume_read(None,['file_owned'],'owner','session',first,'后面接着看','run',repo,None,ok,Blackboard())
    assert resumed['document_reads'][0]['window_start']==4000


def test_failed_history_explains_saved_reason_without_rewriting_or_bypassing_hash(task_store):
    from app.services.agent_runtime.agent_run_store import AgentRunRepository,AgentRunStoreError
    store,session=task_store
    repo=AgentRunRepository(store.mysql)
    submitted=repo.submit(student_id='owner',session_id=session.session_id,idempotency_key='failed-history-projection',
        request_payload={'message':'原问题保留','capability_id':'student-task','interaction_mode':'experts','expert_id':'task-coordinator'})
    rid=submitted['run']['run_id']
    old='只读问答步骤未能完成，当前没有生成可交付答复。'
    repo.fail_run(rid,student_id='owner',reply=old,error_code='turn_token_cap_exceeded')
    before=store.mysql.fetch_one('SELECT final_result_json,final_result_sha256 FROM agent_run WHERE run_id=:id',{'id':rid})
    turn=store.read(session.session_id,'owner')['turns'][0]
    assert turn['message']=='原问题保留' and turn['status']=='failed'
    assert '调用额度不足' in turn['reply'] and old not in turn['reply']
    assert store.mysql.fetch_one('SELECT final_result_json,final_result_sha256 FROM agent_run WHERE run_id=:id',{'id':rid})==before
    store.mysql.execute("UPDATE agent_run SET final_result_json='{}' WHERE run_id=:id",{'id':rid},commit=True)
    with pytest.raises(AgentRunStoreError,match='hash does not match'):store.read(session.session_id,'owner')


def test_root_run_publishes_same_owner_context_and_hashed_history(task_store,monkeypatch):
    from app.services.agent_runtime.agent_run_store import AgentRunRepository
    from app.services.agent_runtime.student_task import run_step,settle
    from app.services.agent_runtime import experts_lead
    from app.services.agent_runtime.media import document_access,session_resources
    from app.services.agent_runtime.blackboard import Blackboard
    store,session=task_store
    monkeypatch.setattr(experts_lead,'task_lead_decide',lambda *a,**k:{'agent':'resume_preparation','goal_summary':'看简历','reply':'','compared_roles':[]})
    monkeypatch.setattr(document_access,'_full_text',lambda _id:('实际简历标记：SQL 项目。',{'filename':'resume.docx'}))
    monkeypatch.setattr(session_resources,'mount_resources',lambda *a,**k:None)
    bound=store.handoff(session,'owner',{'refs':[{'file_id':'file_owned'}],'expected_revision':0},'root-handoff')
    repository=AgentRunRepository(store.mysql)
    snapshot={'message':'分析简历','interaction_mode':'experts','expert_id':'task-coordinator','capability_id':'student-task','task_context_version':bound['revision']}
    admission=repository.submit(student_id='owner',session_id=session.session_id,idempotency_key='root-run-test',request_payload=snapshot,
        creation_callback=lambda conn,rid:store.bind_run(conn,session,'owner',bound['revision'],rid))
    rid=admission['run']['run_id']
    from app.services.agent_runtime.expert_capabilities import resolve_explicit_capability
    repository.persist_plan(rid,student_id='owner',expected_plan_version=0,goal_summary='看简历',steps=[{'role':'qa','goal':'只读','skill_ids':[]}],
        plan_metadata={'capability_binding':resolve_explicit_capability(snapshot).manifest()})
    repository.start_step(rid,student_id='owner',plan_version=1,step_index=1)
    def invoke(packet,*args,**kwargs):
        assert '实际简历标记' in packet.goal
        return {'reply':'你提交的 SQL 项目可作讨论依据，但不是能力认证。','skill_versions':[],'tool_calls':[]}
    result=run_step(mysql=store.mysql,snapshot=snapshot,run_id=rid,owner='owner',session_id=session.session_id,
        repository=repository,claim_token=None,invoke=invoke,packet=None,board=Blackboard())
    repository.finish_step(rid,student_id='owner',plan_version=1,step_index=1,outcome='completed',result={'student_task':result['student_task']})
    repository.begin_finalizing(rid,student_id='owner')
    repository.complete_run(rid,student_id='owner',final_result={'reply':result['reply']},
        completion_callback=lambda conn:settle(store.mysql,conn,session.session_id,'owner',rid))
    restored=store.read(session.session_id,'owner')
    assert restored['latest_result']['reply']==result['reply']
    assert restored['turns'][0]['evidence_summary'][0]['status']=='read'
    assert restored['active_run_id'] is None
    assert store.mysql.fetch_one('SELECT COUNT(*) n FROM student_profile')['n']==0


def test_resume_submission_reuses_original_bytes_and_one_durable_intent(task_store,tmp_path,monkeypatch):
    from app.services.agent_runtime.media import file_store
    from app.services import pipeline_resume_jobs
    from app.services.agent_runtime.task_context import TaskContextConflict
    store,session=task_store
    original=tmp_path/'resume.txt';original.write_text('学生提供的 SQL 项目经历。',encoding='utf-8')
    meta={'file_id':'file_owned','student_id':'owner','status':'ready','filename':'resume.txt','storage_path':str(original),'content_sha256':'unchanged'}
    public={k:v for k,v in meta.items() if k not in {'storage_path','content_sha256'}}
    monkeypatch.setattr(file_store,'get_agent_file_service',lambda:SimpleNamespace(get=lambda _id:public,_load_file=lambda _id:meta))
    deliveries=[]
    monkeypatch.setattr(pipeline_resume_jobs,'enqueue_resume_intent',lambda _db,tid:deliveries.append(tid) or 'queued')
    context=store.handoff(session,'owner',{'refs':[{'file_id':'file_owned'}],'expected_revision':0},'file-handoff')
    result=store.submit_resume(session,'owner','file_owned',context['revision'])
    again=store.submit_resume(session,'owner','file_owned',context['revision'])
    assert result['task_id']==again['task_id']
    assert store.mysql.fetch_one('SELECT COUNT(*) n FROM pipeline_task')['n']==1
    row=store.mysql.fetch_one('SELECT student_id,params_json FROM pipeline_task')
    import json
    assert row['student_id']=='owner' and json.loads(row['params_json'])['file_path']==str(original)
    assert original.read_text(encoding='utf-8')=='学生提供的 SQL 项目经历。'
    assert store.mysql.fetch_one('SELECT COUNT(*) n FROM student_profile')['n']==0
