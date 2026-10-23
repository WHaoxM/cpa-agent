"""Portrait shelf pagination must stay owner-bound and stable."""
import pytest
from flask import Flask, g

from app.api.profile import profile_bp
from app.services import student_portrait_snapshot as snapshots


class SnapshotStore:
    def __init__(self):
        self.calls = []

    def fetch_all(self, sql, params):
        self.calls.append((sql, params))
        return []


def test_snapshot_service_paginates_with_owner_and_stable_order():
    store = SnapshotStore()
    assert snapshots.list_portrait_snapshots(store, student_id='owner', limit=7, offset=14) == []
    sql, params = store.calls[0]
    assert params == {'sid': 'owner', 'lim': 7, 'off': 14}
    assert 's.student_id=:sid' in sql
    assert 's.created_at DESC,s.snapshot_id DESC' in sql
    assert 'OFFSET :off' in sql


@pytest.mark.parametrize('offset', [-1, True, '2'])
def test_snapshot_service_rejects_bad_offset(offset):
    with pytest.raises(ValueError):
        snapshots.list_portrait_snapshots(SnapshotStore(), student_id='owner', offset=offset)


def test_snapshot_route_returns_total_and_rejects_invalid_offset(monkeypatch):
    from app.api import profile
    from app.services import student_workbench
    class Store:
        def fetch_all(self, sql, params):
            assert params['sid'] == 'owner'
            return [{'total': 31}]
    monkeypatch.setattr(profile, 'get_mysql_client', lambda: Store())
    monkeypatch.setattr(student_workbench, 'read_student_workbench', lambda *a, **k: {'portrait': {'snapshot_id': None}})
    seen = []
    monkeypatch.setattr(snapshots, 'list_portrait_snapshots', lambda *a, **k: seen.append(k) or [])
    app = Flask(__name__)
    app.register_blueprint(profile_bp, url_prefix='/api/profile')
    @app.before_request
    def identity():
        g.cpa_principal = {'principal_id': 'account', 'student_id': 'owner'}
    client = app.test_client()
    response = client.get('/api/profile/student/portrait-snapshots?limit=7&offset=14&student_id=other')
    assert response.status_code == 200
    assert response.json['data'] == {'current_snapshot_id': None, 'items': [], 'total': 31, 'limit': 7, 'offset': 14}
    assert seen == [{'student_id': 'owner', 'limit': 7, 'offset': 14}]
    for offset in ('-1', 'x', '1.5'):
        assert client.get('/api/profile/student/portrait-snapshots?offset=' + offset).status_code == 400
