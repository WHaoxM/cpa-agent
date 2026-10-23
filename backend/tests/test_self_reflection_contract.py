"""Versioned self reports: explicit unknown, authoritative snapshots, retries."""
from copy import deepcopy
import json
from datetime import datetime
import pytest
from flask import Flask, g
from app.api import career_bp
from app.api import career
from app.services import self_reflection as reflection


class Store:
    def __init__(self): self.rows = []; self.writes = 0
    def execute(self, sql, params=None):
        if 'submission_id=:submission' in sql:
            return [r for r in self.rows if r['student_id'] == params['owner'] and r['submission_id'] == params['submission']]
        if 'FROM student_self_assessment' in sql:
            return [r for r in reversed(self.rows) if r['student_id'] == params['student_id']][:1]
        raise AssertionError(sql)
    def execute_write(self, sql, params):
        self.writes += 1
        self.rows.append({'assessment_id':params['id'], 'student_id':params['owner'], 'submission_id':params['submission'],
                          'reflection_snapshot':params['snapshot'], 'created_at':datetime(2026,10,22)})


def body():
    pack = reflection.questionnaire('data')
    choices = ['unfamiliar','concept','basic','independent','guide','unsure']
    return {'assessment_kind':'self_reflection', 'submission_id':'reflection-submission-01', 'direction_id':'data',
            'questionnaire_version':pack['version'], 'answers':{q['id']:choices[i % len(choices)] for i,q in enumerate(pack['questions'])}}


@pytest.fixture
def client(monkeypatch):
    db=Store()
    app=Flask(__name__); app.register_blueprint(career_bp,url_prefix='/api/career')
    @app.before_request
    def owner():
        from flask import request
        if not request.headers.get('X-No-Owner'):
            g.cpa_principal={'principal_id':'alice','student_id':'alice'}
    monkeypatch.setattr(career,'get_mysql_client',lambda:db)
    monkeypatch.setattr(career,'_cache_delete',lambda *a:None)
    return app.test_client(),db


def test_catalog_is_versioned_and_has_no_market_or_required_scores(client):
    http,_=client
    result=http.get('/api/career/assessment/questionnaire').json
    assert len(result['data']['directions']) == 20
    pack=http.get('/api/career/assessment/questionnaire?direction_id=data').json['data']
    assert len(pack['questions']) == 8
    assert next(o for o in pack['options'] if o['id']=='unsure')['group']=='unknown'
    assert not {'required','score','heat'} & set().union(*(q.keys() for q in pack['questions']))


def test_save_roundtrip_is_owner_bound_unknown_not_zero_and_idempotent(client):
    http,db=client
    submitted={**body(),'student_id':'bob'}
    first=http.post('/api/career/assessment',json=submitted)
    assert first.status_code==200
    replay=http.post('/api/career/assessment',json=submitted)
    assert replay.json['data']==first.json['data'] and db.writes==1
    loaded=http.get('/api/career/assessment/alice')
    assert loaded.json['data']==first.json['data']
    assert db.rows[0]['student_id']=='alice'
    snapshot=loaded.json['data']['reflection']
    assert 'unsure' in snapshot['answers'].values()
    assert all(isinstance(a,str) for a in snapshot['answers'].values())


@pytest.mark.parametrize('mutation', ['version','direction','question','option','missing','extra','numeric'])
def test_invalid_inputs_never_write(client,mutation):
    http,db=client; data=body()
    if mutation=='version': data['questionnaire_version']='old'
    if mutation=='direction': data['direction_id']='forged'
    if mutation=='question': data['answers']['forged']='unsure'
    if mutation=='option': data['answers']['sql']='fake'
    if mutation=='missing': data['answers'].pop('sql')
    if mutation=='extra': data['score']=99
    if mutation=='numeric': data['answers']['sql']=0
    response=http.post('/api/career/assessment',json=data)
    assert response.status_code in (400,404,409)
    assert db.writes==0


def test_same_submission_with_changed_content_conflicts(client):
    http,db=client; data=body()
    assert http.post('/api/career/assessment',json=data).status_code==200
    data['answers']['sql']='unsure'
    assert http.post('/api/career/assessment',json=data).status_code==409
    assert db.writes==1


def test_replay_and_history_do_not_use_changed_questionnaire(client,monkeypatch):
    http,db=client; data=body()
    saved=http.post('/api/career/assessment',json=data).json
    monkeypatch.setattr(reflection,'questionnaire',lambda *_: (_ for _ in ()).throw(AssertionError('must read saved snapshot')))
    assert http.post('/api/career/assessment',json=data).json==saved
    assert http.get('/api/career/assessment/alice').json==saved


def test_new_submission_keeps_old_snapshot_and_stable_latest(client):
    http,db=client; data=body()
    first=http.post('/api/career/assessment',json=data).json
    old=deepcopy(db.rows[0])
    data['submission_id']='reflection-submission-02'
    data['answers']['sql']='unsure'
    second=http.post('/api/career/assessment',json=data).json
    assert first['data']['assessment_id']!=second['data']['assessment_id']
    assert http.get('/api/career/assessment/alice').json==second
    assert db.rows[0]==old and db.writes==2


def test_private_reads_and_writes_reject_before_database(client,monkeypatch):
    http,_=client
    monkeypatch.setattr(career,'get_mysql_client',lambda:pytest.fail('unauthorized DB access'))
    assert http.get('/api/career/assessment/bob').status_code==403
    for path in ('/api/career/assessment/questionnaire','/api/career/assessment/alice'):
        assert http.get(path,headers={'X-No-Owner':'1'}).status_code==401
    assert http.post('/api/career/assessment',json=body(),headers={'X-No-Owner':'1'}).status_code==401


def test_incomplete_saved_snapshot_not_reconstructed(client):
    http,db=client
    http.post('/api/career/assessment',json=body())
    db.rows[0]['reflection_snapshot']='{}'
    assert http.get('/api/career/assessment/alice').status_code==409


def test_every_migrated_direction_has_unique_complete_questions_and_explicit_unknown():
    for direction in reflection.questionnaire()['directions']:
        pack=reflection.questionnaire(direction['id'])
        answers={q['id']:'unsure' for q in pack['questions']}
        assert len(answers)==len(pack['questions'])
        saved=reflection.make_snapshot({'direction_id':direction['id'],'questionnaire_version':pack['version'],'answers':answers})
        assert saved['answers']==answers
