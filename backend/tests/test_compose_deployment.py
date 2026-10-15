"""Deployment topology contract without starting containers or touching data."""

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]


def _config(layer: str, *, ops: bool = False) -> dict:
    if shutil.which("docker") is None:
        pytest.skip("Docker Compose CLI is not installed")
    env = os.environ.copy()
    env.update({
        "SECRET_KEY": "deployment-contract-placeholder-32-chars",
        "LLM_API_KEY": "test-placeholder",
        "NEO4J_PASSWORD": "test-placeholder",
        "MYSQL_ROOT_PASSWORD": "test-placeholder",
        "CORS_ALLOWED_ORIGINS": "http://127.0.0.1:5174",
        "CPA_MYSQL_PORT": "13306",
        "NEO4J_HTTP_PORT": "17668",
        "NEO4J_BOLT_PORT": "17687",
        "CPA_REDIS_PORT": "16379",
        "BACKEND_PORT": "5001",
    })
    command = ["docker", "compose", "-f", "docker-compose.yml",
               "-f", f"docker-compose.{layer}.yml"]
    if ops:
        command += ["--profile", "ops"]
    command += ["config", "--format", "json"]
    result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True,
                            text=True, check=False)
    if result.returncode:
        if "docker: 'compose' is not a docker command" in result.stderr:
            pytest.skip("Docker Compose v2 is not installed")
        pytest.fail(f"Compose {layer} configuration failed: {result.stderr}")
    return json.loads(result.stdout)["services"]


def _published(service: dict) -> list[tuple[str, str, int]]:
    return sorted(
        (entry.get("host_ip", ""), str(entry["published"]), int(entry["target"]))
        for entry in service.get("ports", [])
    )


def _volumes(service: dict) -> set[tuple[str, str]]:
    return {(entry["source"], entry["target"]) for entry in service["volumes"]}


def test_backend_image_uses_validated_python_minor():
    dockerfile = (ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")
    assert "FROM python:3.10-slim" in dockerfile.splitlines()


def test_backend_build_context_excludes_local_runtime_artifacts():
    patterns = {
        line.strip()
        for line in (ROOT / "backend" / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert {
        ".pytest_cache/",
        "milvus_data/",
        "mineru_output/",
        "output/",
        "celerybeat-schedule",
        "celerybeat-schedule.*",
    } <= patterns
    # Migrations, application code, and default configuration remain build inputs.
    assert not {"app/", "config/", "migrations/", "scripts/"} & patterns


def test_dev_database_ports_are_loopback_only():
    services = _config("dev")
    assert _published(services["mysql"]) == [("127.0.0.1", "13306", 3306)]
    assert _published(services["neo4j"]) == [
        ("127.0.0.1", "17668", 7474),
        ("127.0.0.1", "17687", 7687),
    ]
    assert _published(services["redis"]) == [("127.0.0.1", "16379", 6379)]


def test_production_has_private_databases_and_shared_task_runtime():
    services = _config("prod")
    for name in ("mysql", "neo4j", "redis", "worker", "beat"):
        assert _published(services[name]) == [], name
    assert _published(services["backend"]) == [("127.0.0.1", "5001", 5001)]

    backend = services["backend"]
    for name in ("worker", "beat"):
        service = services[name]
        assert service["image"] == backend["image"]
        assert service["environment"] == backend["environment"]
        assert _volumes(service) == _volumes(backend)
        assert service["healthcheck"] == {"disable": True}
        assert service["depends_on"]["mysql"]["condition"] == "service_healthy"
        assert service["depends_on"]["redis"]["condition"] == "service_healthy"

    environment = backend["environment"]
    assert environment["CPA_DEMO_MODE"] == "false"
    assert environment["CPA_AUTH_COOKIE_SECURE"] == "true"
    assert environment["CORS_ALLOWED_ORIGINS"] == "http://127.0.0.1:5174"
    assert environment["CPA_MEDIA_BROKER_URL"] == "redis://redis:6379/0"
    assert environment["CPA_MILVUS_URI"] == "/app/milvus_data/milvus.db"
    assert "MILVUS_URI" not in environment
    assert {"backend_uploads", "backend_milvus_data"} <= {
        source for source, _ in _volumes(backend)
    }
    worker_command = services["worker"]["command"]
    assert any("cpa.pipeline,cpa.media,cpa.graph" in part for part in worker_command)
    assert "beat" in services["beat"]["command"]


def test_migration_is_explicit_and_uses_same_image():
    default = _config("prod")
    assert "migrate" not in default
    services = _config("prod", ops=True)
    migration = services["migrate"]
    assert migration["image"] == services["backend"]["image"]
    assert migration["environment"]["MYSQL_HOST"] == "mysql"
    assert migration["depends_on"]["mysql"]["condition"] == "service_healthy"
    assert migration["healthcheck"] == {"disable": True}
    assert migration["command"][-2:] == ["upgrade", "head"]
    for name in ("backend", "worker", "beat"):
        assert "migrate" not in services[name].get("depends_on", {})
