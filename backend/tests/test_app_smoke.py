"""Application startup and route-map smoke tests."""

from app import create_app
from app.config import Config


def test_create_app_registers_core_routes():
    app = create_app()

    rules = {str(rule) for rule in app.url_map.iter_rules()}

    assert "/health" in rules
    assert "/api/agent/chat" in rules
    assert "/api/pipeline/list" in rules
    assert "/api/career/landscape" in rules
    assert "/api/admin/knowledge-base/smart-upload" in rules


def test_health_endpoint_returns_ok():
    app = create_app()
    client = app.test_client()

    response = client.get("/health")

    assert response.status_code == 200
    assert response.get_json() == {
        "status": "ok",
        "service": "CPA-Agent Backend",
    }


def test_config_validate_allows_demo_mode(monkeypatch):
    monkeypatch.setattr(Config, "DEMO_MODE", True)
    monkeypatch.setattr(Config, "LLM_API_KEY", None)
    monkeypatch.setattr(Config, "NEO4J_PASSWORD", "")

    assert Config.validate() == []
    assert "LLM_API_KEY 未配置" in Config.validate(strict=True)
