"""Fast adapter checks; real Redis/HTTP coverage lives in the opt-in race suite."""

import pytest
from flask import Flask, Request, Response
from flask_session.redis import RedisSessionInterface
from redis import Redis, RedisError
from werkzeug.test import EnvironBuilder

from app.middleware.account_session import LogoutSafeRedisSessionInterface


class MemoryRedis(Redis):
    def __init__(self):
        self.items = {}
        self.fail_writes = False

    def get(self, name):
        return self.items.get(name)

    def set(self, name, value, ex=None, xx=False):
        if self.fail_writes:
            raise RedisError("test unavailable")
        if xx and name not in self.items:
            return None
        self.items[name] = value
        return True

    def delete(self, name):
        if self.fail_writes:
            raise RedisError("test unavailable")
        return int(self.items.pop(name, None) is not None)


def runtime():
    app = Flask(__name__)
    app.config["SESSION_COOKIE_NAME"] = "cpa_session"
    cache = MemoryRedis()
    upstream = RedisSessionInterface(app, client=cache, key_prefix="account:")
    interface = LogoutSafeRedisSessionInterface.wrap(app, upstream)
    assert interface.serializer is upstream.serializer
    return app, cache, interface


def request_for(sid=None):
    headers = {"Cookie": "cpa_session=" + sid} if sid else {}
    return Request(EnvironBuilder(path="/", headers=headers).get_environ())


@pytest.mark.parametrize("modified", [False, True])
def test_revoked_loaded_sid_is_not_recreated_and_emits_no_cookie(modified):
    app, cache, interface = runtime()
    original = interface.open_session(app, request_for())
    original["_user_id"] = "synthetic"
    interface.save_session(app, original, Response())
    delayed = interface.open_session(app, request_for(original.sid))
    logout = interface.open_session(app, request_for(original.sid))
    logout.clear()
    interface.save_session(app, logout, Response())
    if modified:
        delayed["csrf_token"] = "synthetic"
    response = Response()
    interface.save_session(app, delayed, response)
    assert not cache.items
    assert not response.headers.getlist("Set-Cookie")


def test_native_rotation_creates_new_sid_and_preserves_loaded_payload():
    app, cache, interface = runtime()
    anonymous = interface.open_session(app, request_for())
    anonymous["csrf_token"] = "synthetic"
    interface.save_session(app, anonymous, Response())
    loaded = interface.open_session(app, request_for(anonymous.sid))
    interface.regenerate(loaded)
    loaded["_user_id"] = "synthetic"
    response = Response()
    interface.save_session(app, loaded, response)
    assert loaded.sid != anonymous.sid
    assert "account:" + anonymous.sid not in cache.items
    assert response.headers.getlist("Set-Cookie")
    restored = interface.open_session(app, request_for(loaded.sid))
    assert restored["csrf_token"] == "synthetic"
    assert restored["_user_id"] == "synthetic"


def test_redis_failure_is_not_misreported_as_a_revoked_save():
    app, cache, interface = runtime()
    session = interface.open_session(app, request_for())
    session["_user_id"] = "synthetic"
    interface.save_session(app, session, Response())
    loaded = interface.open_session(app, request_for(session.sid))
    cache.fail_writes = True
    with pytest.raises(RedisError):
        interface.save_session(app, loaded, Response())
    loaded.clear()
    with pytest.raises(RedisError):
        interface.save_session(app, loaded, Response())
