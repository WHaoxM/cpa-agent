"""Production WSGI entrypoint rejects unsafe configuration before app creation."""

from types import SimpleNamespace

from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
import pytest

import production_wsgi


def _config(**overrides):
    values = {
        "DEMO_MODE": False,
        "DEBUG": False,
        "CPA_AUTH_COOKIE_SECURE": True,
        "SECRET_KEY": "a-dedicated-secret-with-more-than-32-characters",
        "CORS_ALLOWED_ORIGINS": ["https://app.example.com"],
        "validate": lambda *, strict: [],
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_valid_production_settings_and_loopback_probe():
    assert production_wsgi.validate_production_config(_config()) == []
    assert production_wsgi.validate_production_config(
        _config(CORS_ALLOWED_ORIGINS=["http://127.0.0.1:5174"])
    ) == []


@pytest.mark.parametrize("overrides", [
    {"DEMO_MODE": True},
    {"DEBUG": True},
    {"CPA_AUTH_COOKIE_SECURE": False},
    {"SECRET_KEY": "short"},
    {"SECRET_KEY": "cpa-agent-insecure-default-do-not-use-in-production"},
    {"CORS_ALLOWED_ORIGINS": ["*"]},
    {"CORS_ALLOWED_ORIGINS": ["http://app.example.com"]},
    {"CORS_ALLOWED_ORIGINS": ["https://app.example.com/path"]},
    {"CORS_ALLOWED_ORIGINS": ["https://app.example.com:bad"]},
    {"CORS_ALLOWED_ORIGINS": []},
])
def test_unsafe_production_settings_are_rejected(overrides):
    assert production_wsgi.validate_production_config(_config(**overrides))


def test_factory_rejects_bad_configuration_before_creating_app(monkeypatch):
    monkeypatch.setattr(production_wsgi, "Config", _config(CORS_ALLOWED_ORIGINS=["*"]))
    monkeypatch.setattr(production_wsgi, "create_app", lambda: pytest.fail("app was created"))
    monkeypatch.setattr(production_wsgi, "require_schema_head",
                        lambda: pytest.fail("database was read"))
    with pytest.raises(RuntimeError, match="CORS_ALLOWED_ORIGINS"):
        production_wsgi.create_production_app()


def test_factory_requires_schema_head_before_creating_app(monkeypatch):
    monkeypatch.setattr(production_wsgi, "Config", _config())
    monkeypatch.setattr(production_wsgi, "require_schema_head",
                        lambda: (_ for _ in ()).throw(RuntimeError("stale schema")))
    monkeypatch.setattr(production_wsgi, "create_app", lambda: pytest.fail("app was created"))
    with pytest.raises(RuntimeError, match="stale schema"):
        production_wsgi.create_production_app()


@pytest.mark.parametrize("stale", [False, True])
def test_schema_guard_reads_ledger_without_running_ddl(monkeypatch, stale):
    expected = ScriptDirectory.from_config(AlembicConfig(
        str(production_wsgi.Path(production_wsgi.__file__).with_name("alembic.ini"))
    )).get_heads()[0]
    rows = [("0001_legacy_schema_baseline" if stale else expected,)]
    queries = []

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def execute(self, query):
            queries.append(query)

        def fetchall(self):
            return rows

    class Connection:
        def cursor(self):
            return Cursor()

        def close(self):
            pass

    monkeypatch.setattr(production_wsgi.pymysql, "connect", lambda **_: Connection())
    config = SimpleNamespace(MYSQL_HOST="mysql", MYSQL_PORT=3306, MYSQL_USER="app",
                             MYSQL_PASSWORD="placeholder", MYSQL_DATABASE="test")
    if stale:
        with pytest.raises(RuntimeError, match="Alembic head"):
            production_wsgi.require_schema_head(config)
    else:
        production_wsgi.require_schema_head(config)
    assert queries == ["SELECT version_num FROM alembic_version"]
