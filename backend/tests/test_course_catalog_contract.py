"""Catalog compatibility and owner-bound chapter writes; no live DB required."""
import pytest
from flask import g, request
from app import create_app
from app.api import learning
from app.config import Config


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(Config, 'DEMO_MODE', False)
    app = create_app()

    @app.before_request
    def owner():
        if not request.headers.get('X-No-Owner'):
            g.cpa_principal = {'principal_id': 'alice', 'student_id': 'alice'}
    return app.test_client()


def test_catalog_keyword_category_total_and_literal_wildcards(client, monkeypatch):
    calls = []
    class DB:
        def execute(self, sql, params=None):
            calls.append((sql, params))
            if 'GROUP BY category' in sql:
                return [{'category': 'cat_002', 'course_count': 5}, {'category': '数据分析', 'course_count': 3}]
            if sql.startswith('SELECT COUNT(*)'):
                return [{'total': 8}]
            if 'DISTINCT category' in sql:
                return [{'category': '数据分析'}]
            return [{'course_id': 'python', 'title': 'SQL_100%', 'category': 'cat_002', 'chapter_count': 0}]
    monkeypatch.setattr(learning, 'get_mysql_client', DB)
    response = client.get('/api/learning/courses?keyword=SQL_100%25&category=数据分析&limit=2&offset=2')
    assert response.status_code == 200
    data = response.json['data']
    assert data['courses'] == [{'course_id': 'python', 'title': 'SQL_100%', 'category': 'cat_002',
                               'category_label': '后端开发', 'chapter_count': 0}]
    assert data['count'] == 1 and data['total'] == 8 and data['offset'] == 2
    assert data['catalog_summary'] == {'total': 8, 'scope': 'all_courses'}
    assert data['category_options'] == [{'value': 'cat_002', 'label': '后端开发', 'course_count': 5},
                                       {'value': '数据分析', 'label': '数据分析', 'course_count': 3}]
    assert data['categories'] == ['cat_002', '数据分析']
    assert calls[0][1] == {'keyword': 'SQL_100%', 'category': '数据分析'}
    assert 'LOCATE(:keyword, title)' in calls[0][0]
    assert 'id DESC' in calls[1][0]
    assert calls[2][1] is None  # Full catalog aggregates never inherit search/page filters.


def test_category_labels_do_not_leak_unknown_ids_or_reencode_stored_text():
    from app.services.course_catalog import category_label
    assert category_label('cat_006') == '测试开发'
    assert category_label('cat_future') == '分类待整理'
    assert category_label('数据分析') == '数据分析'
    assert category_label(None) == '分类未提供'
    assert category_label('Français') == 'Français'
    assert category_label('å‰\x8dç«¯å¼€å\x8f‘') == '分类待整理'


def test_pending_categories_are_one_filter_with_the_full_count():
    from app.services.course_catalog import category_options
    assert category_options([{'category':'cat_future','course_count':2}, {'category':'å‰\x8dç«¯å¼€å\x8f‘','course_count':1}]) == [
        {'value':'__needs_review__','label':'分类待整理','course_count':3}]


def test_pending_category_filter_uses_bound_original_values(client, monkeypatch):
    calls = []
    class DB:
        def execute(self, sql, params=None):
            calls.append((sql,params))
            if 'DISTINCT category' in sql: return [{'category':'cat_future'}, {'category':'数据分析'}]
            if 'GROUP BY category' in sql: return [{'category':'cat_future','course_count':1}]
            if sql.startswith('SELECT COUNT(*)'): return [{'total':1}]
            return [{'course_id':'pending','category':'cat_future','chapter_count':0}]
    monkeypatch.setattr(learning,'get_mysql_client',DB)
    response = client.get('/api/learning/courses?category=__needs_review__')
    assert response.status_code == 200
    assert calls[1][1] == {'pending_category_0':'cat_future'}
    assert 'category IN (:pending_category_0)' in calls[1][0]
    assert response.json['data']['courses'][0]['category_label'] == '分类待整理'


def test_catalog_overlong_keyword_rejected_without_query(client, monkeypatch):
    class DB:
        def execute(self, *args):
            pytest.fail('overlong query must not execute SQL')
    monkeypatch.setattr(learning, 'get_mysql_client', DB)
    assert client.get('/api/learning/courses', query_string={'keyword': 'a' * 201}).status_code == 400


@pytest.mark.parametrize('body', [
    {'progress': True}, {'progress': -1}, {'progress': 101}, {'progress': 'NaN'},
    {'progress': 100, 'completed': 'true'},
])
def test_invalid_progress_never_queries(client, monkeypatch, body):
    monkeypatch.setattr(learning, 'get_mysql_client', lambda: pytest.fail('invalid progress queried DB'))
    response = client.post('/api/learning/learning/progress', json={'course_id': 'course', 'chapter_id': '1', **body})
    assert response.status_code == 400


def test_wrong_course_chapter_cannot_write(client, monkeypatch):
    class DB:
        def execute(self, *args): return []
        def execute_write(self, *args): pytest.fail('orphan progress written')
    monkeypatch.setattr(learning, 'get_mysql_client', DB)
    response = client.post('/api/learning/learning/progress', json={
        'course_id': 'wrong', 'chapter_id': '1', 'progress': 100, 'completed': True})
    assert response.status_code == 404


def test_write_ignores_forged_user_and_verifies_readback(client, monkeypatch):
    class DB:
        def __init__(self): self.saved = None
        def execute(self, sql, params):
            if 'JOIN chapter' in sql: return [{'course_id': 'course', 'chapter_id': '1'}]
            return [self.saved]
        def execute_write(self, sql, params):
            assert params['user_id'] == 'alice'
            self.saved = {k: params[k] for k in ('user_id', 'course_id', 'chapter_id', 'progress', 'completed')}
    db = DB()
    monkeypatch.setattr(learning, 'get_mysql_client', lambda: db)
    response = client.post('/api/learning/learning/progress', json={
        'user_id': 'bob', 'course_id': 'course', 'chapter_id': '1', 'progress': 100, 'completed': True})
    assert response.status_code == 200
    assert response.json['data']['progress']['user_id'] == 'alice'


def test_private_progress_requires_owner_before_query(client, monkeypatch):
    monkeypatch.setattr(learning, 'get_mysql_client', lambda: pytest.fail('unauthorized query'))
    assert client.get('/api/learning/learning/progress/bob').status_code == 403
    assert client.get('/api/learning/learning/progress/alice', headers={'X-No-Owner': '1'}).status_code == 401
    assert client.post('/api/learning/learning/progress', json={}, headers={'X-No-Owner': '1'}).status_code == 401
