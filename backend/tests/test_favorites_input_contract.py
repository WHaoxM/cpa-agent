"""Real registered favorites routes must reject malformed writes before persistence."""

import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from datetime import datetime, timedelta, timezone

import pytest
from flask import Flask, g
from werkzeug.serving import make_server

from app.api import favorites_bp
from app.api._helpers import Principal
from app.config import Config


@pytest.fixture
def favorites_app(monkeypatch):
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    app = Flask(__name__)
    app.register_blueprint(favorites_bp, url_prefix="/api")

    @app.before_request
    def identity():
        g.cpa_principal = Principal("test:owner", "owner", "test")

    return app


@pytest.mark.parametrize("path", ["/api/saved-jobs", "/api/target-roles"])
@pytest.mark.parametrize(
    ("body", "content_type"),
    [
        ("not json", "text/plain"),
        ("{broken", "application/json"),
        ("42", "application/json"),
    ],
)
def test_invalid_write_body_is_client_error_without_repository_call(
    favorites_app, monkeypatch, path, body, content_type
):
    from app.api import favorites

    def unexpected_write(**_kwargs):
        pytest.fail("invalid request must not reach persistence")

    monkeypatch.setattr(favorites.favorites_repo, "save_job", unexpected_write)
    monkeypatch.setattr(favorites.favorites_repo, "add_target_role", unexpected_write)
    response = favorites_app.test_client().post(
        path, data=body, content_type=content_type
    )
    assert response.status_code == 400
    assert response.json["success"] is False


def test_invalid_write_body_over_socket(favorites_app, monkeypatch):
    from app.api import favorites

    calls = []
    monkeypatch.setattr(favorites.favorites_repo, "save_job", lambda **kwargs: calls.append(kwargs))
    server = make_server("127.0.0.1", 0, favorites_app)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        request = Request(
            f"http://127.0.0.1:{server.server_port}/api/saved-jobs",
            data=b"42",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(HTTPError) as error:
            urlopen(request, timeout=5)
        assert error.value.code == 400
        assert calls == []
    finally:
        server.shutdown()
        worker.join(timeout=5)


def test_target_role_rejects_non_text_name_before_repository(favorites_app, monkeypatch):
    from app.api import favorites

    monkeypatch.setattr(
        favorites.favorites_repo,
        "add_target_role",
        lambda **_kwargs: pytest.fail("invalid role must not reach persistence"),
    )
    response = favorites_app.test_client().post(
        "/api/target-roles", json={"role_name": ["工程师"]}
    )
    assert response.status_code == 400
    assert response.json["success"] is False


def test_published_job_favorite_uses_only_publication_reference(favorites_app, monkeypatch):
    from app.api import favorites

    observed = {}

    def save_published_job(**kwargs):
        observed.update(kwargs)
        return 42

    monkeypatch.setattr(favorites.favorites_repo, "save_published_job", save_published_job)
    monkeypatch.setattr(
        favorites.favorites_repo,
        "get_saved_job_by_record",
        lambda record_id, *, user_id: {
            "id": record_id, "saved_job_id": record_id,
            "job_id": "job_public", "publication_status": "current",
            "publication_version": "v7", "content_sha256": "a" * 64,
        },
    )
    response = favorites_app.test_client().post("/api/saved-jobs", json={
        "publication_ref": {
            "job_portrait_id": "job_public",
            "publication_version": "v7",
            "content_sha256": "a" * 64,
        },
        # These fields are attacker-controlled and must never become saved facts.
        "user_id": "another-student",
        "job_title": "伪造岗位",
        "salary": "100K",
        "match_score": 100,
        "required_skills": ["伪造技能"],
    })

    assert response.status_code == 200
    assert response.json["data"]["saved_job_id"] == 42
    assert observed == {
        "user_id": "owner",
        "job_portrait_id": "job_public",
        "publication_version": "v7",
        "content_sha256": "a" * 64,
    }


@pytest.mark.parametrize("reference", [
    None,
    {},
    {"job_portrait_id": "job_public", "publication_version": "v7", "content_sha256": "bad"},
    {"job_portrait_id": "source_csb_123", "publication_version": "v7", "content_sha256": "a" * 64},
])
def test_invalid_publication_favorite_reference_never_reaches_repository(
    favorites_app, monkeypatch, reference
):
    from app.api import favorites

    monkeypatch.setattr(
        favorites.favorites_repo,
        "save_published_job",
        lambda **kwargs: pytest.fail("invalid reference must not reach persistence"),
    )
    body = {"publication_ref": reference}
    response = favorites_app.test_client().post("/api/saved-jobs", json=body)
    assert response.status_code == 400
    assert response.json["success"] is False


def test_revoked_or_changed_publication_reference_is_conflict(favorites_app, monkeypatch):
    from app.api import favorites

    def conflict(**_kwargs):
        raise favorites.SavedJobPublicationError("岗位资料已变化，请刷新后再收藏")

    monkeypatch.setattr(favorites.favorites_repo, "save_published_job", conflict)
    response = favorites_app.test_client().post("/api/saved-jobs", json={
        "publication_ref": {
            "job_portrait_id": "job_public",
            "publication_version": "v7",
            "content_sha256": "a" * 64,
        },
    })
    assert response.status_code == 409
    assert response.json["success"] is False


def test_record_delete_is_exact_and_owner_scoped(favorites_app, monkeypatch):
    from app.api import favorites

    calls = []
    present = {"saved_job_id": 19, "job_id": "job_public"}

    def get_by_record(record_id, *, user_id):
        calls.append(("read", record_id, user_id))
        return present.copy() if present else None

    def delete_record(record_id, *, user_id):
        calls.append(("delete", record_id, user_id))
        present.clear()

    monkeypatch.setattr(favorites.favorites_repo, "get_saved_job_by_record", get_by_record)
    monkeypatch.setattr(favorites.favorites_repo, "delete_saved_job_record", delete_record)
    response = favorites_app.test_client().delete("/api/saved-jobs/records/19")

    assert response.status_code == 200
    assert calls == [
        ("read", 19, "owner"),
        ("delete", 19, "owner"),
        ("read", 19, "owner"),
    ]


def test_publication_favorite_projection_hides_internal_reference_ids():
    from app.repositories.favorites_repo import FavoritesRepository
    from app.services.job_publication import content_sha256

    source = "数据分析实习生岗位。要求 Python 能力。"
    now = datetime.now(timezone.utc)
    snapshot = {
        "position_name": "数据分析实习生",
        "source_uri": "https://jobs.example.org/role/7",
        "source_text": source,
        "source_text_sha256": __import__("hashlib").sha256(source.encode()).hexdigest(),
        "reviewed_by": "admin_7",
        "reviewed_at": now.isoformat(),
        "source_fetched_at": (now - timedelta(minutes=1)).isoformat(),
        "source_license": "operator-supplied",
        "review_state": "approved",
        "required_skills": [{"name": "Python", "level": "必须"}],
        "job_profile": {"degree_requirement": None, "major_requirements": [],
                        "major_strict": False, "required_certs": []},
        "field_evidence": {
            "position_name": {"status": "explicit", "excerpt": "数据分析实习生"},
            "degree_requirement": {"status": "not_stated", "excerpt": None},
            "major_requirements": {"status": "not_stated", "excerpt": None},
            "major_strict": {"status": "not_stated", "excerpt": None},
            "required_certs": {"status": "not_stated", "excerpt": None},
            "required_skills": {"status": "explicit", "excerpt": "要求 Python 能力"},
        },
    }
    row = {
        "id": 19, "user_id": "owner", "job_id": "job_public",
        "job_title": "数据分析实习生", "required_skills": snapshot["required_skills"],
        "publication_id": 7, "bound_publication_id": 7,
        "bound_job_portrait_id": "job_public", "bound_publication_version": "v2",
        "bound_snapshot_json": snapshot, "bound_content_sha256": content_sha256(snapshot),
        "bound_publication_status": "published", "bound_published_at": None,
        "latest_publication_id": 7,
    }

    projected = FavoritesRepository._project_saved_job(row)

    assert projected["publication_status"] == "current"
    assert projected["publication_version"] == "v2"
    assert projected["publication_snapshot"]["position_name"] == "数据分析实习生"
    assert "publication_id" not in projected
    assert "bound_publication_id" not in projected

    superseded = FavoritesRepository._project_saved_job({**row, "latest_publication_id": 8})
    assert superseded["publication_status"] == "superseded"
    legacy = FavoritesRepository._project_saved_job({
        "id": 20, "user_id": "owner", "job_id": "legacy-job", "job_title": "学生原始记录",
        "publication_id": None,
    })
    assert legacy["publication_status"] == "legacy_unverified"
    assert legacy["publication_snapshot"] is None
