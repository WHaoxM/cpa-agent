"""Logout fencing for Flask-Session 0.8's Redis adapter.

Keep upstream SID rotation, encoding, expiration and cookie rules. Its normal
SET can recreate a session deleted by logout while an earlier request runs;
loaded SIDs instead use Redis SET XX, and a rejected save emits no old cookie.
"""

from flask_session.redis import RedisSession, RedisSessionInterface


class _RevokedSessionSave(Exception):
    pass


class _LoadedRedisSession(RedisSession):
    def __init__(self, initial=None, sid=None, permanent=None):
        super().__init__(initial=initial, sid=sid, permanent=permanent)
        # Request-local provenance, not a new field in the stored payload.
        self.loaded_sid = sid if initial is not None else None


class LogoutSafeRedisSessionInterface(RedisSessionInterface):
    session_class = _LoadedRedisSession

    @classmethod
    def wrap(cls, app, upstream):
        guarded = cls(app, client=upstream.client, key_prefix=upstream.key_prefix,
                      use_signer=upstream.use_signer, permanent=upstream.permanent,
                      sid_length=upstream.sid_length)
        guarded.serializer = upstream.serializer
        return guarded

    def _upsert_session(self, session_lifetime, session, store_id):
        if session.loaded_sid == session.sid:
            saved = self.client.set(
                name=store_id, value=self.serializer.encode(session),
                ex=int(session_lifetime.total_seconds()), xx=True,
            )
            if not saved:
                raise _RevokedSessionSave()
        else:
            # Fresh anonymous sessions and upstream-regenerated login SIDs.
            super()._upsert_session(session_lifetime, session, store_id)
        session.loaded_sid = session.sid

    def save_session(self, app, session, response):
        try:
            super().save_session(app, session, response)
        except _RevokedSessionSave:
            # Do not set OR delete a cookie: this response belongs to an older
            # request and must not overwrite a subsequent login's cookie.
            response.vary.add("Cookie")

