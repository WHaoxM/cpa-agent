"""Deployment topology contract without starting containers or touching data."""

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


ROOT = Path(__file__).resolve().parents[2]


def _config(layer: str, *, ops: bool = False, host_proxy: bool = False) -> dict:
    if shutil.which("docker") is None:
        pytest.skip("Docker Compose CLI is not installed")
    env = os.environ.copy()
    env.update({
        "SECRET_KEY": "deployment-contract-placeholder-32-chars",
        "LLM_API_KEY": "test-placeholder",
        "NEO4J_PASSWORD": "test-placeholder",
        "MYSQL_ROOT_PASSWORD": "test-placeholder",
        "MYSQL_APP_USER": "cpa_app",
        "MYSQL_APP_PASSWORD": "test-app-placeholder",
        "CORS_ALLOWED_ORIGINS": "http://127.0.0.1:5174",
        "CPA_MYSQL_PORT": "13306",
        "NEO4J_HTTP_PORT": "17668",
        "NEO4J_BOLT_PORT": "17687",
        "CPA_REDIS_PORT": "16379",
        "BACKEND_PORT": "5001",
        "BACKEND_BIND_PORT": "15001",
        "CPA_COMPOSE_PROJECT_NAME": "cpa-agent-contract-test",
        "CPA_BACKEND_IMAGE": "registry.example.invalid/cpa-agent@sha256:" + "0" * 64,
        "CPA_MYSQL_IMAGE": "mysql@sha256:" + "1" * 64,
        "CPA_NEO4J_IMAGE": "neo4j@sha256:" + "2" * 64,
        "CPA_REDIS_IMAGE": "redis@sha256:" + "3" * 64,
    })
    for name in ("MYSQL", "NEO4J", "REDIS", "BACKEND", "WORKER", "BEAT", "MIGRATE"):
        env[f"CPA_{name}_MEM_LIMIT"] = "1g"
        env[f"CPA_{name}_CPUS"] = "1"
    command = ["docker", "compose", "-f", "docker-compose.yml",
               "-f", f"docker-compose.{layer}.yml"]
    if host_proxy:
        command += ["-f", "docker-compose.prod.host-proxy.yml"]
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
    assert "FROM python:3.10-slim@sha256:6ff506466f8b1e981719b468cc0023b8ef000b983cdec0eafe119d01f379e31d" in dockerfile.splitlines()
    assert "--only-binary=:all:" in dockerfile
    assert "apt-get" not in dockerfile
    assert "urllib.request.urlopen" in dockerfile
    assert "requirements-linux-amd64-py310.lock" in dockerfile
    assert "--mount=type=cache,target=/root/.cache/pip" in dockerfile
    assert "USER 10001:10001" in dockerfile


def test_backend_image_installs_all_direct_requirements_with_hash_lock():
    dockerfile = (ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY requirements-linux-amd64-py310.lock ./" in dockerfile
    assert "--require-hashes" in dockerfile
    assert "--no-compile" in dockerfile
    assert "-r requirements-linux-amd64-py310.lock" in dockerfile
    assert "https://download.pytorch.org/whl/cpu" in dockerfile
    constraints = (ROOT / "backend" / "constraints-linux-amd64-py310.txt").read_text(
        encoding="utf-8"
    ).splitlines()
    versions = {}
    for line in constraints:
        if not line or line.startswith("#"):
            continue
        requirement = Requirement(line)
        name = canonicalize_name(requirement.name)
        assert name not in versions
        assert str(requirement.specifier).startswith("==")
        versions[name] = str(requirement.specifier)
    direct = (
        line.split("#", 1)[0].strip()
        for line in (ROOT / "backend" / "requirements.txt").read_text(
            encoding="utf-8"
        ).splitlines()
    )
    assert all(canonicalize_name(Requirement(line).name) in versions
               for line in direct if line)
    assert versions["torch"] == "==2.6.0+cpu"

    lock_lines = (ROOT / "backend" / "requirements-linux-amd64-py310.lock").read_text(
        encoding="utf-8"
    ).splitlines()
    locked = {}
    current = None
    for line in lock_lines:
        if not line or line.startswith("#") or line.lstrip().startswith("#"):
            continue
        if line.startswith("    --hash=sha256:"):
            assert current is not None
            digest = line.split("sha256:", 1)[1].rstrip(" \\")
            assert len(digest) == 64 and all(c in "0123456789abcdef" for c in digest)
            locked[current]["hashes"].add(digest)
            continue
        requirement = Requirement(line.rstrip(" \\"))
        name = canonicalize_name(requirement.name)
        assert name not in locked
        assert str(requirement.specifier).startswith("==")
        locked[name] = {"version": str(requirement.specifier), "hashes": set()}
        current = name
    assert set(locked) == set(versions)
    assert all(locked[name]["version"] == version for name, version in versions.items())
    assert all(item["hashes"] for item in locked.values())


def test_backend_image_default_entry_is_fail_closed_production_wsgi():
    dockerfile = (ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")
    assert 'CMD ["gunicorn",' in dockerfile
    assert 'production_wsgi:create_production_app()' in dockerfile
    assert 'CMD ["python", "run.py"]' not in dockerfile


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
        assert services[name]["logging"] == {
            "driver": "local", "options": {"max-size": "10m", "max-file": "5"},
        }
    assert _published(services["backend"]) == []
    assert "5001" in services["backend"]["expose"]
    for name in ("mysql", "neo4j", "redis", "backend", "worker", "beat"):
        assert int(services[name]["mem_limit"]) == 1073741824
        assert services[name]["cpus"] == 1.0
        assert "@sha256:" in services[name]["image"]

    backend = services["backend"]
    assert "build" not in backend
    assert backend["image"].endswith("@sha256:" + "0" * 64)
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
    assert environment["MYSQL_USER"] == "cpa_app"
    assert environment["MYSQL_PASSWORD"] == "test-app-placeholder"
    assert environment["CORS_ALLOWED_ORIGINS"] == "http://127.0.0.1:5174"
    assert environment["CPA_MEDIA_BROKER_URL"] == "redis://redis:6379/0"
    assert environment["CPA_AGENT_QUEUE"] == "cpa.agent"
    assert environment["CPA_MILVUS_URI"] == "/app/milvus_data/milvus.db"
    assert "MILVUS_URI" not in environment
    assert {"backend_uploads", "backend_milvus_data"} <= {
        source for source, _ in _volumes(backend)
    }
    worker_command = services["worker"]["command"]
    assert any("cpa.pipeline,cpa.media,cpa.graph,cpa.agent" in part for part in worker_command)
    assert "beat" in services["beat"]["command"]


def test_host_proxy_override_is_loopback_only_and_has_no_default_fixed_host_port():
    services = _config("prod", host_proxy=True)
    assert _published(services["backend"]) == [("127.0.0.1", "15001", 5001)]


def test_production_http_uses_wsgi_server_not_flask_development_server():
    backend = _config("prod")["backend"]
    command = backend["command"]
    assert "gunicorn" in command
    assert "production_wsgi:create_production_app()" in command
    assert "run.py" not in command


def test_migration_is_explicit_and_uses_same_image():
    default = _config("prod")
    assert "migrate" not in default
    services = _config("prod", ops=True)
    migration = services["migrate"]
    assert migration["image"] == services["backend"]["image"]
    assert migration["environment"]["MYSQL_HOST"] == "mysql"
    assert migration["environment"]["MYSQL_USER"] == "root"
    assert migration["environment"]["MYSQL_PASSWORD"] == "test-placeholder"
    assert services["mysql"]["environment"]["MYSQL_USER"] == "cpa_app"
    assert services["mysql"]["environment"]["MYSQL_ROOT_PASSWORD"] == "test-placeholder"
    assert services["neo4j"]["environment"]["NEO4J_AUTH"] == "neo4j/test-placeholder"
    assert migration["depends_on"]["mysql"]["condition"] == "service_healthy"
    assert migration["healthcheck"] == {"disable": True}
    assert migration["command"][-2:] == ["upgrade", "head"]
    assert int(migration["mem_limit"]) == 1073741824
    assert migration["cpus"] == 1.0
    for name in ("backend", "worker", "beat"):
        assert "migrate" not in services[name].get("depends_on", {})
