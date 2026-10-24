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


@auth_bp.after_request
def no_cached_account_response(response):
    response.headers["Cache-Control"] = "no-store"
    return response


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
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+\Z")
_PHONE = re.compile(r"[0-9+() .-]{3,20}\Z")
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


_PROFILE_COLUMNS = ("name", "email", "phone", "signature")
_NOTIFICATION_CHANNELS = {
    "email": {"available": False, "reason": "邮件发送服务未接入"},
    "system": {"available": False, "reason": "站内消息可查看，但偏好尚未接入通知投递"},
    "sms": {"available": False, "reason": "短信发送服务未接入"},
}


def _iso_or_none(value):
    return value.isoformat() if value is not None and hasattr(value, "isoformat") else (
        str(value) if value is not None else None
    )


def _account_profile_payload(row):
    return {
        "id": row["id"],
        "username": row["username"],
        "name": row["name"],
        "email": row["email"],
        "phone": row["phone"],
        "signature": row["signature"],
        "role": row["role"],
        "status": row["status"],
        "created_at": _iso_or_none(row["created_at"]),
        "last_login_at": _iso_or_none(row["last_login_at"]),
        "auth_method": row["auth_method"],
        "revision": int(row["profile_revision"]),
    }


def _account_profile_row(connection, account_id: str, *, lock: bool = False):
    suffix = " FOR UPDATE" if lock else ""
    return connection.execute(text(
        "SELECT id,username,name,email,phone,signature,role,status,created_at,"
        "last_login_at,profile_revision,"
        "CASE WHEN password_hash IS NOT NULL THEN 'account_password' ELSE NULL END AS auth_method "
        "FROM user_account WHERE id=:id" + suffix
    ), {"id": account_id}).mappings().first()


@auth_bp.get("/profile")
def account_profile():
    """Return profile and account facts from the signed-in database account."""
    if not current_user.is_authenticated:
        return jsonify({"success": False, "error": "未登录"}), 401
    try:
        row = get_mysql_client().execute_single(
            "SELECT id,username,name,email,phone,signature,role,status,created_at,"
            "last_login_at,profile_revision,"
            "CASE WHEN password_hash IS NOT NULL THEN 'account_password' ELSE NULL END AS auth_method "
            "FROM user_account WHERE id=:id LIMIT 1",
            {"id": current_user.id},
        )
        if row is None or row["status"] != "active":
            return jsonify({"success": False, "error": "会话已失效"}), 401
        return jsonify({"success": True, "data": {"profile": _account_profile_payload(row)}})
    except Exception:
        return _service_unavailable()


def _profile_value(field: str, value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("个人资料格式不正确")
    value = value.strip()
    if not value:
        return None
    limits = {"name": 100, "email": 100, "phone": 20, "signature": 255}
    if len(value) > limits[field]:
        raise ValueError("个人资料长度超出限制")
    if field == "email" and not _EMAIL.fullmatch(value):
        raise ValueError("邮箱格式不正确")
    if field == "phone" and (
        not _PHONE.fullmatch(value) or not any(character.isdigit() for character in value)
    ):
        raise ValueError("手机号格式不正确")
    return value


@auth_bp.patch("/profile")
def update_account_profile():
    """Save whitelisted profile fields with optimistic concurrency control."""
    if not current_user.is_authenticated:
        return jsonify({"success": False, "error": "未登录"}), 401
    data = _body()
    expected_revision = data.get("expected_revision")
    supplied_fields = set(data) & set(_PROFILE_COLUMNS)
    if (type(expected_revision) is not int or expected_revision < 0
            or not supplied_fields or set(data) - (set(_PROFILE_COLUMNS) | {"expected_revision"})):
        return jsonify({"success": False, "error": "个人资料请求格式不正确"}), 400
    try:
        values = {field: _profile_value(field, data[field])
                  for field in supplied_fields}
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400

    try:
        with get_mysql_client().engine.begin() as connection:
            row = _account_profile_row(connection, current_user.id, lock=True)
            if row is None or row["status"] != "active":
                return jsonify({"success": False, "error": "会话已失效"}), 401
            if int(row["profile_revision"]) != expected_revision:
                return jsonify({"success": False, "error": "资料已在其他会话更新，请先刷新", "code": "STALE_PROFILE", "profile": _account_profile_payload(row)}), 409
            for field in _PROFILE_COLUMNS:
                values.setdefault(field, row[field])
            result = connection.execute(text(
                "UPDATE user_account SET name=:name,email=:email,phone=:phone,"
                "signature=:signature,profile_revision=profile_revision+1,updated_at=NOW() "
                "WHERE id=:id AND profile_revision=:revision"
            ), {**values, "id": current_user.id, "revision": expected_revision})
            if result.rowcount != 1:
                return jsonify({"success": False, "error": "资料已在其他会话更新，请先刷新", "code": "STALE_PROFILE"}), 409
            saved = _account_profile_row(connection, current_user.id)
            if saved is None:
                raise RuntimeError("Updated account profile could not be read back")
        return jsonify({"success": True, "data": {"profile": _account_profile_payload(saved)}})
    except Exception:
        return _service_unavailable()


def _notification_payload(row):
    preferences = ({
        "email": row["email_preference"],
        "system": row["system_preference"],
        "sms": row["sms_preference"],
    } if row is not None else {"email": None, "system": None, "sms": None})
    return {
        "configured": any(value is not None for value in preferences.values()),
        "preferences": preferences,
        "revision": int(row["revision"]) if row is not None else 0,
        "channels": _NOTIFICATION_CHANNELS,
    }


@auth_bp.get("/notification-preferences")
def notification_preferences():
    if not current_user.is_authenticated:
        return jsonify({"success": False, "error": "未登录"}), 401
    try:
        row = get_mysql_client().execute_single(
            "SELECT email_preference,system_preference,sms_preference,revision "
            "FROM user_account_notification_preference WHERE user_id=:id LIMIT 1",
            {"id": current_user.id},
        )
        return jsonify({"success": True, "data": _notification_payload(row)})
    except Exception:
        return _service_unavailable()


@auth_bp.put("/notification-preferences")
def save_notification_preferences():
    if not current_user.is_authenticated:
        return jsonify({"success": False, "error": "未登录"}), 401
    data = _body()
    expected_revision = data.get("expected_revision")
    preferences = data.get("preferences")
    allowed = {"email", "system", "sms"}
    if (type(expected_revision) is not int or expected_revision < 0
            or not isinstance(preferences, dict) or set(preferences) != allowed
            or any(value is not None and type(value) is not bool
                   for value in preferences.values())
            or set(data) != {"expected_revision", "preferences"}):
        return jsonify({"success": False, "error": "通知偏好格式不正确"}), 400
    try:
        with get_mysql_client().engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO user_account_notification_preference (user_id,revision) "
                "VALUES (:id,0) ON DUPLICATE KEY UPDATE user_id=VALUES(user_id)"
            ), {"id": current_user.id})
            row = connection.execute(text(
                "SELECT email_preference,system_preference,sms_preference,revision "
                "FROM user_account_notification_preference WHERE user_id=:id FOR UPDATE"
            ), {"id": current_user.id}).mappings().first()
            if row is None:
                raise RuntimeError("Account preferences row could not be created")
            if int(row["revision"]) != expected_revision:
                return jsonify({"success": False, "error": "通知偏好已在其他会话更新，请先刷新", "code": "STALE_PREFERENCES", **_notification_payload(row)}), 409
            result = connection.execute(text(
                "UPDATE user_account_notification_preference SET "
                "email_preference=:email,system_preference=:system,sms_preference=:sms,"
                "revision=revision+1,updated_at=NOW(6) "
                "WHERE user_id=:id AND revision=:revision"
            ), {"email": preferences["email"], "system": preferences["system"],
                "sms": preferences["sms"], "id": current_user.id,
                "revision": expected_revision})
            if result.rowcount != 1:
                return jsonify({"success": False, "error": "通知偏好已在其他会话更新，请先刷新", "code": "STALE_PREFERENCES"}), 409
            saved = connection.execute(text(
                "SELECT email_preference,system_preference,sms_preference,revision "
                "FROM user_account_notification_preference WHERE user_id=:id"
            ), {"id": current_user.id}).mappings().first()
            if saved is None:
                raise RuntimeError("Saved preferences could not be read back")
        return jsonify({"success": True, "data": _notification_payload(saved)})
    except Exception:
        return _service_unavailable()
