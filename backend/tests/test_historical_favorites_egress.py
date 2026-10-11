from flask import Flask, g
import pytest

from app.api import favorites_bp
from app.api import favorites
from app.api._helpers import Principal
from app.config import Config
from app.services.agent_runtime.tool_result_channels import build_ui_view
from app.services.agent_runtime.tool_egress import sanitize_tool_call_record


def client(monkeypatch, authenticated=True):
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    app = Flask(__name__)
    app.register_blueprint(favorites_bp, url_prefix="/api/favorites")
    if authenticated:
        @app.before_request
        def identity():
            g.cpa_principal = Principal("auth:a", "owner_a", "test")
    return app.test_client()


@pytest.mark.parametrize("state", ["running", "pending", "queued", "partial", "needs_input"])
def test_structured_tool_state_survives_both_egress_layers(state):
    result = build_ui_view("pipeline_run", {"status": state, "success": False,
                          "summary": "任务ID task_fake 查询结果 (9 条) 没有错误或失败"})
    assert result["status"] == state
    public = sanitize_tool_call_record(result)
    assert public["status"] == state
    assert "已完成" not in public["summary"]
    assert "task_id" not in public["parameters"]


def test_prose_never_becomes_status_or_task_id():
    public = sanitize_tool_call_record({"tool_name": "query", "result": "task_fake 已完成"})
    assert public["status"] == "unverified"
    assert "task_id" not in public["parameters"]
    good = build_ui_view("query", {"success": True, "message": "错误处理与失败恢复指南"})
    assert good["status"] == "done"
    assert "未成功" not in good["summary"]
    unknown = build_ui_view("query", {"summary": "全部成功"})
    assert unknown["status"] == "unverified"


def test_favorites_requires_principal_and_real_input_id(monkeypatch):
    c = client(monkeypatch, False)
    assert c.post("/api/favorites/saved-jobs", json={"user_id": "owner_a"}).status_code == 401
    c = client(monkeypatch)
    assert c.post("/api/favorites/saved-jobs", json={"job_title": "Fake"}).status_code == 400


def test_save_uses_principal_and_exact_insert_id(monkeypatch):
    seen = {}
    def save(**kwargs):
        seen.update(kwargs)
        return 42
    def read(job_id, **kwargs):
        assert kwargs == {"user_id": "owner_a", "record_id": 42}
        return {"id": 42, "job_id": job_id}
    monkeypatch.setattr(favorites.favorites_repo, "save_job", save)
    monkeypatch.setattr(favorites.favorites_repo, "get_saved_job", read)
    c = client(monkeypatch)
    response = c.post("/api/favorites/saved-jobs", json={"job_id": "external_1", "user_id": "victim"})
    assert response.status_code == 200
    assert seen["user_id"] == "owner_a"
    assert response.json["data"]["saved_job_id"] == 42
    assert response.json["data"]["source_verified"] is False
    monkeypatch.setattr(favorites.favorites_repo, "get_saved_job", lambda *a, **k: {"id": 41})
    assert c.post("/api/favorites/saved-jobs", json={"job_id": "external_1"}).status_code == 503


def test_delete_missing_or_foreign_record_is_not_success(monkeypatch):
    def read(job_id, *, user_id):
        assert user_id == "owner_a"
        return None
    monkeypatch.setattr(favorites.favorites_repo, "get_saved_job", read)
    monkeypatch.setattr(favorites.favorites_repo, "delete_saved_job",
                        lambda *a, **k: pytest.fail("must not delete"))
    assert client(monkeypatch).delete("/api/favorites/saved-jobs/victim").status_code == 404


def test_delete_requires_absence_readback(monkeypatch):
    monkeypatch.setattr(favorites.favorites_repo, "get_saved_job", lambda *a, **k: {"id": 42})
    monkeypatch.setattr(favorites.favorites_repo, "delete_saved_job", lambda *a, **k: None)
    assert client(monkeypatch).delete("/api/favorites/saved-jobs/job1").status_code == 503
