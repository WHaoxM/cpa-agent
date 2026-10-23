import pytest
from app.services.agent_runtime.run_failure import failure_reply


@pytest.mark.parametrize('code,expected,absent',[
    ('turn_token_cap_exceeded','调用额度不足','权限'),
    ('turn_token_usage_unverified','不表示预算已经用完','额度不足'),
    ('estimated_input_over_window','模型可接收的窗口','权限'),
    ('runtime_release_changed_during_step','执行期间发生更新','预算'),
    ('task_answer_validation_failed','资料与格式核对','稍后重新提交'),
    ('ValueError','读取或答复核对','能力已经确认'),
    ('TimeoutError','及时响应','预算耗尽'),
    ('raw_database_password_uri','分析未完成','raw_database_password_uri'),
])
def test_public_failure_is_specific_when_known_and_never_echoes_internal_error(code,expected,absent):
    reply=failure_reply(code)
    assert expected in reply and absent not in reply
    assert '材料仍保留' in reply


def test_executor_persists_budget_reason_instead_of_generic_qa_failure(monkeypatch):
    from backend.tests.test_agent_run_executor import _Repository,_Dispatch,_plan
    from app.services.agent_runtime.agent_run_executor import AgentRunExecutor
    from app.services.agent_runtime.blackboard import Blackboard
    from app.services.agent_runtime import mode_runtime
    repo=_Repository();executor=AgentRunExecutor(repo,_Dispatch())
    monkeypatch.setattr(executor,'_renew_or_stop',lambda *a:None)
    monkeypatch.setattr(executor,'_release_matches',lambda *a:True)
    def invoke(*a,**k):raise RuntimeError('turn_token_cap_exceeded')
    monkeypatch.setattr(mode_runtime,'_invoke_subagent',invoke)
    assert executor._execute_step('run_'+'1'*32,'owner_a','2'*32,
        {'message':'复盘','interaction_mode':'agent'},_plan(),_plan()['steps'][0],
        board=Blackboard(),session_id='session_a') is True
    assert repo.failed[0][1]['error_code']=='turn_token_cap_exceeded'
    assert '调用额度不足' in repo.failed[0][1]['reply']
    assert '只读问答步骤' not in repo.failed[0][1]['reply']
