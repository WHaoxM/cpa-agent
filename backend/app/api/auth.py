"""Platform-managed accounts. Only the database grants roles and resource identity."""

from __future__ import annotations

import re
import uuid
from datetime import datetime

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError, VerificationError
from flask import Blueprint, current_app, jsonify, request, session
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import current_user, login_user, logout_user
from flask_wtf.csrf import generate_csrf
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from ..services.db import get_mysql_client

auth_bp = Blueprint("auth", __name__)


def _account_limit_key():
    """Keep app-factory test/deployment limit namespaces distinct per request.

    Flask-Limiter's extension instance remembers its first app's key prefix;
    this request-local namespace also works when multiple app factories live in
    one process, without trusting a client-supplied identity header.
    """
    prefix = current_app.config.get("RATELIMIT_KEY_PREFIX", "cpa-account-auth")
    return f"{prefix}:{get_remote_address()}"


limiter = Limiter(key_func=_account_limit_key, default_limits=[])
_hasher = PasswordHasher()
_USERNAME = re.compile(r"[A-Za-z0-9_.-]{3,50}\Z")
_DUMMY_HASH = _hasher.hash("never-a-valid-account-password")


def _account_row(*, username: str | None = None, user_id: str | None = None):
    if username is not None:
        return get_mysql_client().execute_single(
            "SELECT id, username, password_hash, name, role, status, auth_epoch FROM user_account "
            "WHERE username = :value LIMIT 1", {"value": username},
        )
    return get_mysql_client().execute_single(
        "SELECT id, username, password_hash, name, role, status, auth_epoch FROM user_account "
        "WHERE id = :value LIMIT 1", {"value": user_id},
    )


def _public_user(row):
    return {"id": row["id"], "username": row["username"],
            "name": row["name"], "role": row["role"]}


def _body():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _credentials(data):
    username, password = data.get("username"), data.get("password")
    if not isinstance(username, str) or not _USERNAME.fullmatch(username):
        return None
    if not isinstance(password, str) or not 12 <= len(password) <= 128:
        return None
    return username, password


def _service_unavailable():
    return jsonify({"success": False, "error": "账号服务未配置或暂不可用"}), 503


@auth_bp.before_request
def _require_account_runtime():
    if not current_app.extensions.get("cpa_account_auth_enabled", False):
        return _service_unavailable()
    if not current_app.extensions.get("cpa_account_session_schema_ready", False):
        return _service_unavailable()


@auth_bp.get("/csrf")
def csrf_token():
    response = jsonify({"success": True, "csrfToken": generate_csrf()})
    response.headers["Cache-Control"] = "no-store"
    return response


@auth_bp.post("/register")
@limiter.limit("3 per hour")
def register():
    if current_user.is_authenticated:
        return jsonify({"success": False, "error": "请先退出当前账号"}), 409
    data = _body()
    credentials = _credentials(data)
    name = data.get("name")
    if credentials is None or (name is not None and
                               (not isinstance(name, str) or len(name) > 100)):
        return jsonify({"success": False, "error": "账号资料不符合要求"}), 400
    username, password = credentials
    account_id = f"acct_{uuid.uuid4().hex}"
    try:
        get_mysql_client().execute_write(
            "INSERT INTO user_account (id, username, password_hash, name, role, status, "
            "created_at, updated_at) VALUES (:id, :username, :password_hash, :name, "
            "'student', 'active', NOW(), NOW())",
            {"id": account_id, "username": username,
             "password_hash": _hasher.hash(password), "name": name or None},
        )
    except IntegrityError:
        return jsonify({"success": False, "error": "用户名已被使用"}), 409
    except Exception:
        return _service_unavailable()
    return jsonify({"success": True, "user": {"id": account_id,
                    "username": username, "name": name or None,
                    "role": "student"}}), 201


@auth_bp.post("/login")
@limiter.limit("5 per minute")
def login():
    if current_user.is_authenticated:
        return jsonify({"success": False, "error": "请先退出当前账号"}), 409
    credentials = _credentials(_body())
    if credentials is None:
        return jsonify({"success": False, "error": "用户名或密码错误"}), 401
    username, password = credentials
    try:
        row = _account_row(username=username)
        try:
            valid = _hasher.verify(row["password_hash"] if row else _DUMMY_HASH,
                                   password)
        except (InvalidHashError, VerifyMismatchError, VerificationError):
            valid = False
        if not valid or row is None or row["status"] != "active":
            return jsonify({"success": False, "error": "用户名或密码错误"}), 401
        from ..middleware.account_auth import AccountUser
        current_app.session_interface.regenerate(session)
        login_user(AccountUser(row))
        session["_cpa_auth_epoch"] = int(row["auth_epoch"])
        session.permanent = True
        get_mysql_client().execute_write(
            "UPDATE user_account SET last_login_at = :when, last_login_ip = :ip "
            "WHERE id = :id", {"when": datetime.now(),
                                 "ip": request.remote_addr, "id": row["id"]},
        )
    except Exception:
        session.clear()
        return _service_unavailable()
    return jsonify({"success": True, "user": _public_user(row)})


@auth_bp.post("/logout")
def logout():
    logout_user()
    session.clear()
    return jsonify({"success": True})


@auth_bp.post("/change-password")
@limiter.limit("5 per hour")
def change_password():
    """Change a signed-in account password and revoke every prior session.

    The account row is locked while the old secret and auth_epoch are checked.
    The database trigger advances auth_epoch on the hash update, so even an
    idle browser on another device cannot revive its old cookie.
    """
    if not current_user.is_authenticated:
        return jsonify({"success": False, "error": "未登录"}), 401
    data = _body()
    old_password = data.get("current_password")
    new_password = data.get("new_password")
    if (not isinstance(old_password, str) or not 1 <= len(old_password) <= 128
            or not isinstance(new_password, str) or not 12 <= len(new_password) <= 128):
        return jsonify({"success": False, "error": "密码资料不符合要求"}), 400
    account_id = current_user.id
    expected_epoch = session.get("_cpa_auth_epoch")
    try:
        with get_mysql_client().engine.begin() as conn:
            row = conn.execute(text(
                "SELECT password_hash,status,auth_epoch FROM user_account "
                "WHERE id=:id FOR UPDATE"), {"id": account_id}).mappings().first()
            if (row is None or row["status"] != "active"
                    or type(expected_epoch) is not int
                    or row["auth_epoch"] != expected_epoch):
                session.clear()
                return jsonify({"success": False, "error": "会话已失效"}), 401
            try:
                valid = _hasher.verify(row["password_hash"], old_password)
            except (InvalidHashError, VerifyMismatchError, VerificationError):
                valid = False
            if not valid:
                return jsonify({"success": False, "error": "当前密码错误"}), 401
            if old_password == new_password:
                return jsonify({"success": False, "error": "新密码须与当前密码不同"}), 400
            new_hash = _hasher.hash(new_password)
            updated = conn.execute(text(
                "UPDATE user_account SET password_hash=:hash,updated_at=NOW() "
                "WHERE id=:id AND auth_epoch=:epoch"),
                {"hash": new_hash, "id": account_id, "epoch": expected_epoch})
            if updated.rowcount != 1:
                raise RuntimeError("密码更新未通过版本核对")
            saved = conn.execute(text(
                "SELECT password_hash,auth_epoch FROM user_account WHERE id=:id"),
                {"id": account_id}).mappings().one()
            if saved["password_hash"] != new_hash or saved["auth_epoch"] != expected_epoch + 1:
                raise RuntimeError("密码更新未通过事务内回读")
    except Exception:
        return _service_unavailable()
    logout_user()
    session.clear()
    response = jsonify({"success": True, "reauthentication_required": True})
    response.headers["Cache-Control"] = "no-store"
    return response


@auth_bp.get("/me")
def me():
    if not current_user.is_authenticated:
        return jsonify({"success": False, "error": "未登录"}), 401
    return jsonify({"success": True, "user": _public_user(current_user.row)})
