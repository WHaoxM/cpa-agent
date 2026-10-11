"""Fast contract checks around the durable HTTP idempotency seam."""

from flask import Flask
import pytest

from app.middleware.cors_middleware import CorsMiddleware
from app.services.agent_runtime.turn_idempotency import (
    InvalidIdempotencyKey,
    canonical_request_sha256,
    scoped_key_sha256,
    validate_idempotency_key,
)


def test_key_validation_and_owner_session_namespace():
    key = validate_idempotency_key("turn_12345678")
    assert key == "turn_12345678"
    assert scoped_key_sha256("owner-a", "session-a", key) != scoped_key_sha256(
        "owner-b", "session-a", key,
    )
    assert scoped_key_sha256("owner-a", "session-a", key) != scoped_key_sha256(
        "owner-a", "session-b", key,
    )
    with pytest.raises(InvalidIdempotencyKey):
        validate_idempotency_key("short")
    with pytest.raises(InvalidIdempotencyKey):
        validate_idempotency_key("contains a space")


def test_canonical_request_hash_is_mapping_order_independent_but_semantic():
    first = canonical_request_sha256({
        "message": "hello",
        "attachment_ids": ["a", "b"],
        "plan_mode": False,
    })
    reordered = canonical_request_sha256({
        "plan_mode": False,
        "attachment_ids": ["a", "b"],
        "message": "hello",
    })
    changed = canonical_request_sha256({
        "message": "different",
        "attachment_ids": ["a", "b"],
        "plan_mode": False,
    })
    assert first == reordered
    assert first != changed


def test_cors_allows_key_and_exposes_replay_metadata():
    app = Flask(__name__)
    CorsMiddleware(app)

    @app.route("/probe", methods=["OPTIONS"])
    def probe():
        return "", 204

    response = app.test_client().options(
        "/probe",
        headers={
            "Origin": "https://frontend.example",
            "Access-Control-Request-Headers": "Idempotency-Key, Content-Type",
        },
    )
    assert response.status_code == 204
    assert "Idempotency-Key" in response.headers["Access-Control-Allow-Headers"]
    assert "Idempotency-Replayed" in response.headers[
        "Access-Control-Expose-Headers"
    ]
    assert "Retry-After" in response.headers["Access-Control-Expose-Headers"]
