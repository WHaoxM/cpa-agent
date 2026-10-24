"""Fail-closed WSGI entrypoint for the single-host production Compose layer.

The development ``run.py`` performs a few Windows-specific conveniences and
starts Flask's built-in server. Gunicorn and the Windows Waitress entrypoint
import this factory instead. Keep the production checks here so a
misconfigured runtime cannot serve requests.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
import pymysql

from app import create_app
from app.config import Config


def validate_production_config(config=None) -> list[str]:
    """Return safe labels for configuration errors, never secret values."""
    config = config or Config
    errors = list(config.validate(strict=True))
    if config.DEMO_MODE:
        errors.append("CPA_DEMO_MODE must be false")
    if config.DEBUG:
        errors.append("FLASK_DEBUG must be false")
    if not config.CPA_AUTH_COOKIE_SECURE:
        errors.append("CPA_AUTH_COOKIE_SECURE must be true")
    secret = str(config.SECRET_KEY or "")
    if len(secret) < 32 or secret == "cpa-agent-insecure-default-do-not-use-in-production":
        errors.append("SECRET_KEY must be a non-default value of at least 32 characters")

    origins = list(config.CORS_ALLOWED_ORIGINS or [])
    if not origins:
        errors.append("CORS_ALLOWED_ORIGINS must contain an explicit origin")
    for origin in origins:
        value = str(origin).strip()
        try:
            parsed = urlsplit(value)
            # ``hostname`` alone accepts malformed ports; reading ``port``
            # forces validation without persisting or logging the input.
            parsed.port
        except ValueError:
            errors.append("CORS_ALLOWED_ORIGINS contains an invalid or non-HTTPS remote origin")
            break
        valid = (
            value != "*"
            and parsed.scheme in ("http", "https")
            and bool(parsed.hostname)
            and not parsed.username
            and not parsed.password
            and not parsed.path
            and not parsed.query
            and not parsed.fragment
            and value == f"{parsed.scheme}://{parsed.netloc}"
        )
        if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
            valid = False
        if not valid:
            errors.append("CORS_ALLOWED_ORIGINS contains an invalid or non-HTTPS remote origin")
            break
    return errors


def require_schema_head(config=None) -> None:
    """Read the version ledger; never run DDL from an HTTP process."""
    config = config or Config
    alembic = AlembicConfig(str(Path(__file__).with_name("alembic.ini")))
    expected = set(ScriptDirectory.from_config(alembic).get_heads())
    connection = pymysql.connect(
        host=config.MYSQL_HOST,
        port=config.MYSQL_PORT,
        user=config.MYSQL_USER,
        password=config.MYSQL_PASSWORD,
        database=config.MYSQL_DATABASE,
        connect_timeout=3,
        read_timeout=3,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT version_num FROM alembic_version")
            actual = {row[0] for row in cursor.fetchall()}
    finally:
        connection.close()
    if actual != expected:
        raise RuntimeError("MySQL schema is not at this image's Alembic head")


def create_production_app():
    errors = validate_production_config()
    if errors:
        raise RuntimeError("Invalid production configuration: " + "; ".join(errors))
    require_schema_head()
    return create_app()
