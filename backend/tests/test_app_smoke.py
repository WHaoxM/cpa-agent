"""Application startup and route-map smoke tests."""

from app import create_app
from app.config import Config


def test_create_app_registers_core_routes():
    app = create_app()

    rules = {str(rule) for rule in app.url_map.iter_rules()}

    assert "/health" in rules
    assert "/api/agent/chat" in rules
    assert "/api/agent/runs" in rules
    assert "/api/agent/runs/<run_id>" in rules
    assert "/api/agent/runs/<run_id>/events" in rules
    assert "/api/agent/runs/<run_id>/cancel" in rules
    assert "/api/agent/files" in rules
    assert "/api/agent/voice/turns" in rules
    assert "/api/agent/session/<session_id>/resources" in rules
    assert "/api/agent/memory/<student_id>" in rules
    assert "/api/agent/session/<session_id>/archive" in rules
    assert "/api/pipeline/list" in rules
    assert "/api/career/landscape" in rules
    assert "/api/career/role-intro" in rules
    assert "/api/career/infer-role" in rules
    assert "/api/messages" in rules
    assert "/api/ability-graph" in rules
    assert "/api/learning/course-system-graph" in rules
    assert "/api/agent/career-suggestions" in rules
    assert "/api/ai/growth-analysis" in rules
    assert "/api/report/generate" in rules
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
    # strict 路径还会扫 failover provider key；统一清空以免本机 .env 干扰
    monkeypatch.setattr("app.config.get_failover_order", lambda: [])
    monkeypatch.setattr("app.config.resolve_provider_api_key", lambda _p: None)

    assert Config.validate() == []
    strict_errors = Config.validate(strict=True)
    assert "LLM_API_KEY 未配置" in strict_errors
    assert "NEO4J_PASSWORD 未配置 (可设为空字符串)" in strict_errors
