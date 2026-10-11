"""Real MySQL journal/confirmation checks; opt in with CPA_TEST_PIPELINE_MYSQL=1."""
import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import text

from app.services.db.mysql_client import MySQLClient
from app.services.pipeline import confirm_resume_candidate
from app.services.pipeline_events import install_pipeline_event_journal, read_pipeline_events


@pytest.fixture
def journal():
    if os.getenv('CPA_TEST_PIPELINE_MYSQL') != '1':
        pytest.skip('requires explicit MySQL integration opt-in')
    db = MySQLClient()
    install_pipeline_event_journal(db)
    owner = 'evtest_' + uuid.uuid4().hex
    tid = 'evtask_' + uuid.uuid4().hex
    candidate = {'candidate_profile': {'skills': ['Python']},
                 'structure_validation': {'ok': True}, 'source_evidence': {'blocks': []}}
    db.execute("INSERT INTO pipeline_task(task_id,student_id,pipeline,status,result_json) "
               "VALUES(:tid,:sid,'L0_parse_resume','needs_input',:r)",
               {'tid': tid, 'sid': owner, 'r': json.dumps(candidate)}, commit=True)
    try:
        yield db, owner, tid
    finally:
        with db.engine.begin() as conn:
            conn.execute(text('DELETE FROM pipeline_task_event WHERE task_id=:tid'), {'tid': tid})
            conn.execute(text('DELETE FROM pipeline_task WHERE task_id=:tid'), {'tid': tid})
            conn.execute(text('DELETE FROM student_profile WHERE student_id=:sid'), {'sid': owner})
        db.close()


def test_replay_and_duplicate_confirmation(journal):
    db, sid, tid = journal
    first = read_pipeline_events(db, student_id=sid, task_id=tid)
    assert [e['status'] for e in first['events']] == ['needs_input']
    confirm_resume_candidate(db, student_id=sid, task_id=tid)
    confirm_resume_candidate(db, student_id=sid, task_id=tid)
    db.close()  # New connection/engine, no in-process event memory.
    tail = read_pipeline_events(db, student_id=sid, task_id=tid, cursor=first['cursor'])
    assert [e['status'] for e in tail['events']] == ['completed']
    assert tail['task']['status'] == 'completed'
    assert tail['resume_state'] == 'not_available'
    assert read_pipeline_events(db, student_id=sid, task_id=tid, cursor=tail['cursor'])['events'] == []


def test_rollback_has_no_event_and_no_false_terminal(journal):
    db, sid, tid = journal
    with pytest.raises(RuntimeError):
        with db.engine.begin() as conn:
            conn.execute(text("UPDATE pipeline_task SET status='completed' WHERE task_id=:tid"), {'tid': tid})
            raise RuntimeError('crash before commit')
    replay = read_pipeline_events(db, student_id=sid, task_id=tid)
    assert replay['task']['status'] == 'needs_input'
    assert [e['status'] for e in replay['events']] == ['needs_input']


def test_owner_isolation_and_failed_confirmation_rolls_back_placeholder(journal):
    db, sid, tid = journal
    other = 'other_' + uuid.uuid4().hex
    with pytest.raises(LookupError):
        read_pipeline_events(db, student_id=other, task_id=tid)
    with pytest.raises(LookupError):
        confirm_resume_candidate(db, student_id=other, task_id=tid)
    assert not db.fetch_all('SELECT student_id FROM student_profile WHERE student_id=:sid', {'sid': other})


def test_concurrent_confirmation_is_idempotent(journal):
    db, sid, tid = journal
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(confirm_resume_candidate, db, student_id=sid, task_id=tid) for _ in range(2)]
        assert all(f.result()['status'] == 'completed' for f in futures)
    replay = read_pipeline_events(db, student_id=sid, task_id=tid)
    assert [e['status'] for e in replay['events']] == ['needs_input', 'completed']


def test_confirmation_write_failure_rolls_back_profile_task_and_event(journal, monkeypatch):
    from app.services.pipeline_events import ConfirmationTransaction
    db, sid, tid = journal
    original = ConfirmationTransaction.execute

    def fail_task_write(self, sql, parameters=None, commit=False):
        if 'UPDATE pipeline_task' in sql:
            raise RuntimeError('injected failure after profile write')
        return original(self, sql, parameters, commit)

    monkeypatch.setattr(ConfirmationTransaction, 'execute', fail_task_write)
    with pytest.raises(RuntimeError):
        confirm_resume_candidate(db, student_id=sid, task_id=tid)
    assert not db.fetch_all('SELECT student_id FROM student_profile WHERE student_id=:sid', {'sid': sid})
    replay = read_pipeline_events(db, student_id=sid, task_id=tid)
    assert replay['task']['status'] == 'needs_input'
    assert [e['status'] for e in replay['events']] == ['needs_input']


def test_cursor_pagination_and_same_status_has_no_event(journal):
    db, sid, tid = journal
    for status in ['running', 'running', 'failed']:
        db.execute('UPDATE pipeline_task SET status=:st WHERE task_id=:tid', {'tid': tid, 'st': status}, commit=True)
    cursor, statuses = 0, []
    while True:
        page = read_pipeline_events(db, student_id=sid, task_id=tid, cursor=cursor, limit=1)
        statuses.extend(e['status'] for e in page['events'])
        cursor = page['cursor']
        if not page['has_more']:
            break
    assert statuses == ['needs_input', 'running', 'failed']
    assert page['task']['status'] == 'failed'


def test_flask_owner_and_cursor_contract(journal, monkeypatch):
    from flask import Flask
    from types import SimpleNamespace
    from app.api import pipeline as api
    db, sid, tid = journal
    app = Flask(__name__)
    app.register_blueprint(api.pipeline_bp, url_prefix='/api/pipeline')
    monkeypatch.setattr(api, 'get_mysql_client', lambda: db)
    monkeypatch.setattr(api, 'resolve_request_scope', lambda: SimpleNamespace(student_id=sid))
    client = app.test_client()
    response = client.get('/api/pipeline/events/' + tid)
    assert response.status_code == 200
    assert response.json['data']['events'][0]['status'] == 'needs_input'
    assert client.get('/api/pipeline/events/' + tid + '?cursor=-1').status_code == 400
    monkeypatch.setattr(api, 'resolve_request_scope', lambda: SimpleNamespace(student_id='other'))
    assert client.get('/api/pipeline/events/' + tid).status_code == 404
