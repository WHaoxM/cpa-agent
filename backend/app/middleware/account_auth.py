"""Thin Flask extensions adapter for trusted self-managed accounts."""

from __future__ import annotations

from datetime import timedelta

import click
from flask import g, jsonify, request, session
from flask_login import LoginManager, UserMixin, current_user
from flask_limiter.errors import RateLimitExceeded
from flask_session import Session
from flask_wtf.csrf import CSRFError, CSRFProtect
from redis import Redis

from ..api._helpers import Principal

_DEFAULT_SECRET = "cpa-agent-insecure-default-do-not-use-in-production"


class AccountUser(UserMixin):
    def __init__(self, row):
        self.row = row
        self.id = row["id"]


def install_account_auth(app):
    """Enable only with a strong explicit secret in strict mode; never silently downgrade."""
    from ..api.auth import auth_bp, limiter, _account_row, _hasher, _USERNAME
    from ..services.db import get_mysql_client

    secret = app.config.get("SECRET_KEY")
    from ..config import Config
    enabled = (not app.config.get("DEMO_MODE", True) and
               not Config.DEMO_MODE and
               isinstance(secret, str) and len(secret) >= 32 and
               secret != _DEFAULT_SECRET and
               not secret.lower().startswith("your-"))
    app.extensions["cpa_account_auth_enabled"] = enabled
    app.register_blueprint(auth_bp, url_prefix="/api/auth")

    @app.cli.command("provision-admin")
    @click.option("--username", required=True)
    @click.option("--name", default=None)
    def provision_admin(username, name):
        """Trusted local CLI only; never exposed as an HTTP promotion endpoint."""
        if not _USERNAME.fullmatch(username):
            raise click.UsageError("Username must be 3-50 ASCII letters/digits/._-")
        password = click.prompt("Password", hide_input=True, confirmation_prompt=True)
        if not 12 <= len(password) <= 128:
            raise click.UsageError("Password must be 12-128 characters")
        import uuid
        from sqlalchemy.exc import IntegrityError
        try:
            get_mysql_client().execute_write(
                "INSERT INTO user_account (id, username, password_hash, name, role, status, "
                "created_at, updated_at) VALUES (:id, :username, :password_hash, :name, "
                "'admin', 'active', NOW(), NOW())",
                {"id": f"acct_{uuid.uuid4().hex}", "username": username,
                 "password_hash": _hasher.hash(password), "name": name},
            )
        except IntegrityError as exc:
            raise click.ClickException("Username already exists") from exc
        click.echo("Admin account provisioned")

    @app.cli.command("migrate-account-session-epoch")
    def migrate_account_session_epoch():
        """Add the account epoch column and invalidation trigger to an existing DB."""
        from ..services.account_session_schema import migrate_account_session_schema
        try:
            migrate_account_session_schema()
        except Exception as exc:
            raise click.ClickException(f"Account session migration failed: {exc}") from exc
        click.echo("Account session epoch migration verified; restart backend workers")

    if not enabled:
        return

    from ..services.account_session_schema import account_session_schema_ready
    try:
        schema_ready = account_session_schema_ready()
    except Exception as exc:
        app.logger.warning("Account session schema unavailable: %s", exc)
        schema_ready = False
    app.extensions["cpa_account_session_schema_ready"] = schema_ready
    if not schema_ready:
        app.logger.warning("Account auth remains fail-closed until epoch migration and restart")

    from ..api.auth import limiter
    app.config.update(
        SESSION_TYPE="redis",
        SESSION_REDIS=Redis.from_url(app.config["REDIS_URL"], socket_timeout=2),
        SESSION_KEY_PREFIX="cpa:account:session:",
        SESSION_COOKIE_NAME="cpa_session",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=app.config.get("CPA_AUTH_COOKIE_SECURE", True),
        PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
        WTF_CSRF_CHECK_DEFAULT=False,
        RATELIMIT_STORAGE_URI=app.config["REDIS_URL"],
        RATELIMIT_HEADERS_ENABLED=True,
    )
    app.config.setdefault("RATELIMIT_KEY_PREFIX", "cpa-account-auth")
    Session(app)
    from .account_session import LogoutSafeRedisSessionInterface
    app.session_interface = LogoutSafeRedisSessionInterface.wrap(app, app.session_interface)
    login_manager = LoginManager()
    login_manager.init_app(app)
    csrf = CSRFProtect(app)
    limiter.init_app(app)

    @app.errorhandler(RateLimitExceeded)
    def account_rate_limited(error):
        return jsonify({"success": False, "error": "请求过于频繁，请稍后重试",
                        "code": "RATE_LIMITED"}), 429

    @login_manager.user_loader
    def load_user(user_id):
        if not app.extensions.get("cpa_account_session_schema_ready", False):
            g.cpa_account_db_unavailable = True
            return None
        try:
            row = _account_row(user_id=user_id)
        except Exception:
            # Fail closed; never promote a stale signed cookie on DB outage.
            g.cpa_account_db_unavailable = True
            return None
        if (row is None or row["status"] != "active"
                or type(session.get("_cpa_auth_epoch")) is not int
                or session["_cpa_auth_epoch"] != row["auth_epoch"]):
            return None
        return AccountUser(row)

    @app.before_request
    def inject_verified_identity():
        if current_user.is_authenticated:
            g.cpa_principal = Principal(current_user.id, current_user.id,
                                         source="account")
            g.cpa_admin = current_user.row["role"] == "admin"
        elif session.get("_user_id"):
            # A disabled/deleted account is not a development fallback candidate.
            if request.endpoint not in {"auth.logout", "auth.csrf_token"}:
                if getattr(g, "cpa_account_db_unavailable", False):
                    return jsonify({"success": False, "error": "账号数据库暂不可用"}), 503
                session.clear()
                return jsonify({"success": False, "error": "会话已失效"}), 401

    @app.before_request
    def csrf_for_cookie_writes():
        if request.method in {"POST", "PUT", "PATCH", "DELETE"} and (
            request.endpoint in {"auth.login", "auth.register", "auth.logout"}
            or session.get("_user_id")
        ):
            csrf.protect()

    @app.errorhandler(CSRFError)
    def csrf_rejected(error):
        return jsonify({"success": False, "error": "CSRF 验证失败"}), 400
