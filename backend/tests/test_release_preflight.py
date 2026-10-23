"""Release admission inspects resolved topology without leaking credentials."""

from copy import deepcopy

import pytest

from scripts.ops.release_preflight import check_configuration


def _model():
    digest = "registry.invalid/cpa@sha256:" + "a" * 64
    services = {
        name: {"image": digest, "mem_limit": str(1024**3), "cpus": 1.0,
               "logging": {"driver": "local", "options": {"max-size": "10m", "max-file": "5"}}}
        for name in ("mysql", "neo4j", "redis", "backend", "worker", "beat")
    }
    services["mysql"]["environment"] = {
        "MYSQL_ROOT_PASSWORD": "root-password", "MYSQL_PASSWORD": "app-password",
    }
    services["neo4j"]["environment"] = {"NEO4J_AUTH": "neo4j/neo-password"}
    services["backend"]["environment"] = {
        "CPA_DEMO_MODE": "false", "FLASK_DEBUG": "false",
        "CPA_AUTH_COOKIE_SECURE": "true",
        "CORS_ALLOWED_ORIGINS": "https://app.example.invalid",
        "SECRET_KEY": "a-unique-production-key-over-32-chars",
        "LLM_API_KEY": "provider-secret",
        "MYSQL_USER": "cpa_app", "MYSQL_PASSWORD": "app-password",
        "NEO4J_PASSWORD": "neo-password",
    }
    for name in ("worker", "beat"):
        services[name]["environment"] = deepcopy(services["backend"]["environment"])
    return {"services": services}


def test_valid_static_configuration_is_not_full_release_approval():
    assert check_configuration(_model(), available_memory=16 * 1024**3) == []


def test_static_gate_rejects_unpinned_public_and_oversized_configuration():
    model = deepcopy(_model())
    model["services"]["backend"]["image"] = "cpa:latest"
    model["services"]["backend"]["ports"] = [{"host_ip": "0.0.0.0", "published": "5001"}]
    model["services"]["backend"]["environment"]["CORS_ALLOWED_ORIGINS"] = "http://example.invalid"
    model["services"]["backend"]["environment"]["MYSQL_PASSWORD"] = "root-password"
    errors = check_configuration(model, available_memory=4 * 1024**3)
    assert any("digest" in error for error in errors)
    assert any("loopback" in error for error in errors)
    assert any("HTTPS" in error for error in errors)
    assert any("non-root" in error for error in errors)
    assert any("memory" in error for error in errors)
    assert "root-password" not in str(errors)


def test_static_gate_rejects_checked_in_development_credentials():
    model = deepcopy(_model())
    model["services"]["mysql"]["environment"]["MYSQL_ROOT_PASSWORD"] = "!Rootmysql123"
    model["services"]["neo4j"]["environment"]["NEO4J_AUTH"] = "neo4j/neo4jpass"
    model["services"]["backend"]["environment"]["NEO4J_PASSWORD"] = "neo4jpass"
    errors = check_configuration(model)
    assert any("MySQL credentials" in error for error in errors)
    assert any("Neo4j credentials" in error for error in errors)


@pytest.mark.parametrize("service,key,value", [
    ("worker", "CPA_DEMO_MODE", "true"),
    ("beat", "SECRET_KEY", "different-production-key-over-32-chars"),
])
def test_static_gate_rejects_divergent_backend_runtime_environment(service, key, value):
    model = _model()
    model["services"][service]["environment"][key] = value
    errors = check_configuration(model)
    assert any("same backend environment" in error for error in errors)
