"""Public pathway contracts: no implied eligibility, fake pay or graph cycles."""
from copy import deepcopy
import pytest
from app.services.career_pathway import content_digest, validate_payload, read_pathway


@pytest.fixture
def payload():
    evidence = {'url': 'https://docs.python.org/3/tutorial/', 'note': '官方入门教程，推荐先学习语言基础'}
    return {'schema_version': 'career-pathway/v1', 'role_id': 'backend-development',
        'role_name': '后端开发', 'revision': 'backend-test-v1',
        'skills': [{'skill_id': i, 'name': n, 'tier': 'foundation',
                    'requirement_kind': 'recommended', 'evidence': evidence}
                   for i,n in [(1,'Python'), (2,'Flask')]],
        'courses': [{'course_id': 'python-intro', 'name': 'Python', 'skill_ids': [1],
                     'chapter_ids': [1], 'source_url': evidence['url']}],
        'prerequisites': [{'from_skill_id': 1, 'to_skill_id': 2, 'evidence': evidence}],
        'jobs': [{'fact_id': 'official-backend', 'title': '后端开发工程师', 'employer': '测试来源',
            'locations': ['北京'], 'summary': '官网事实摘要', 'source_uri': 'https://example.org/job',
            'source_sha256': 'a'*64, 'source_fetched_at': '2026-10-22T00:00:00Z',
            'vacancy_validity': 'unknown', 'usage': 'reference_only', 'scoring_ready': False,
            'salary_quote': None, 'requirements': [
                {'kind': 'any_of', 'label': '编程语言任选其一', 'values': ['Python','Go','Java']},
                {'kind': 'not_stated', 'label': '证书未说明', 'values': []}]}],
        'salary_references': [{'reference_id': 'initial-reference', 'kind': 'estimate', 'city': '深圳',
            'experience_level': '初级', 'lower': 8000, 'upper': 12000, 'currency': 'CNY', 'period': 'month',
            'method': '合成测试区间，不是对外报价', 'basis': [{'title':'测试依据', **evidence}], 'as_of': '2026-10-22',
            'uncertainty': '非统计置信区间', 'disclaimer': '估算，不代表招聘方承诺'}]}


def test_any_of_and_unknown_survive_without_scoring(payload):
    result = validate_payload(payload)
    assert result['jobs'][0]['requirements'][0]['values'] == ['Python','Go','Java']
    assert result['jobs'][0]['requirements'][1]['values'] == []
    assert not result['jobs'][0]['scoring_ready']


@pytest.mark.parametrize('change', ['cycle','unknown_skill','duplicate_skill','invented_unknown','score','fake_quote','bad_salary','nan','unsafe_source','statistic','extra_field','integer_revision','evidence_extra'])
def test_invalid_facts_fail_closed(payload, change):
    if change == 'cycle':
        payload['prerequisites'].append(dict(from_skill_id=2,to_skill_id=1,evidence=payload['skills'][0]['evidence']))
    elif change == 'unknown_skill': payload['courses'][0]['skill_ids'] = [999]
    elif change == 'duplicate_skill': payload['skills'].append(payload['skills'][0])
    elif change == 'invented_unknown': payload['jobs'][0]['requirements'][1]['values'] = ['无证书要求']
    elif change == 'score': payload['jobs'][0]['scoring_ready'] = True
    elif change == 'fake_quote': payload['jobs'][0]['salary_quote'] = {'lower': 10000}
    elif change == 'bad_salary': payload['salary_references'][0]['upper'] = 1
    elif change == 'nan': payload['salary_references'][0]['lower'] = float('nan')
    elif change == 'unsafe_source': payload['jobs'][0]['source_uri'] = 'javascript:alert(1)'
    elif change == 'statistic': payload['salary_references'][0]['kind'] = 'observed_statistics'
    elif change == 'extra_field': payload['unversioned_extra'] = 'ignored'
    elif change == 'integer_revision': payload['revision'] = 1
    elif change == 'evidence_extra': payload['skills'][0]['evidence']['unversioned'] = True
    with pytest.raises(ValueError): validate_payload(payload)


def test_read_envelope_not_part_of_business_digest(payload):
    digest = content_digest(payload)
    p = {**payload, 'content_sha256': digest, 'published_at': 'now', 'projection_status': {}}
    assert content_digest(p) == digest
    changed = deepcopy(payload); changed['salary_references'][0]['upper'] += 1
    assert content_digest(changed) != digest


def test_corrupt_snapshot_is_not_read_as_current(payload):
    class DB:
        def execute_single(self, *args):
            return {'snapshot_json': payload, 'content_sha256': '0'*64}
    with pytest.raises(RuntimeError, match='integrity'): read_pathway(DB(), 'backend-development')


def test_valid_digest_does_not_override_identity(payload):
    class DB:
        def execute_single(self, *args):
            return {'snapshot_json': payload, 'content_sha256': content_digest(payload),
                    'role_id': 'another-role', 'revision': payload['revision'], 'schema_version': payload['schema_version']}
    with pytest.raises(RuntimeError, match='identity'): read_pathway(DB(), 'backend-development')


def test_import_lock_outlives_business_commit():
    from contextlib import contextmanager
    from types import SimpleNamespace
    from app.services.career_pathway import _import_transaction
    events = []
    class Connection:
        def execute(self, sql):
            events.append('release' if 'RELEASE_LOCK' in str(sql) else 'acquire')
            return SimpleNamespace(scalar=lambda: 1)
        def commit(self): events.append('connection.commit')
        @contextmanager
        def begin(self):
            events.append('business.begin')
            yield self
            events.append('business.commit')
    @contextmanager
    def connect(): yield Connection()
    db = SimpleNamespace(engine=SimpleNamespace(connect=connect))
    with _import_transaction(db, True): events.append('write')
    assert events.index('business.commit') < events.index('release')


def test_graph_paths_category_query_binds_path(monkeypatch):
    from flask import Flask
    from app.api import career
    seen = []
    class Graph:
        def execute_query(self, cypher, params):
            seen.append((cypher,params)); return []
    monkeypatch.setattr(career, 'get_neo4j_client', lambda: Graph())
    app = Flask(__name__)
    with app.test_request_context('/api/career/paths?category=后端&depth=99'):
        response = career.get_career_paths()
    assert response.status_code == 200
    assert 'MATCH path =' in seen[0][0]
    assert '*1..5' in seen[0][0]


def test_public_routes_are_read_only_and_bound_query(monkeypatch):
    from flask import Flask
    from app.api import career_bp
    from app.api import career_pathway as api
    monkeypatch.setattr(api, 'get_mysql_client', lambda: object())
    monkeypatch.setattr(api, 'read_pathway', lambda *args: None)
    app = Flask(__name__); app.register_blueprint(career_bp, url_prefix='/api/career')
    client = app.test_client()
    assert client.get('/api/career/role-pathways/backend-development').status_code == 404
    assert client.post('/api/career/role-pathways/backend-development', json={}).status_code == 405
    assert client.get('/api/career/role-pathways/backend-development/search?q=').status_code == 400
    assert client.get('/api/career/role-pathways/backend-development/search?q='+'a'*161).status_code == 400
