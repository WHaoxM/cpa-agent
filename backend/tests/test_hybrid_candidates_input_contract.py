"""Reject malformed public retrieval requests before touching dependencies."""

import uuid

import pytest

from app import create_app
from app.config import Config


@pytest.fixture
def client_and_calls(monkeypatch):
    from app.api import match

    calls = []
    monkeypatch.setattr(match, "hybrid_recommend", lambda *args, **kwargs:
                        calls.append((args, kwargs)) or {"candidates": []})
    config = type("HybridInputConfig", (Config,), {
        "SECRET_KEY": "hybrid-input-test-only-secret-with-32-bytes",
        "DEMO_MODE": False,
        "RATELIMIT_KEY_PREFIX": f"hybrid-input-{uuid.uuid4().hex}",
    })
    return create_app(config).test_client(), calls


@pytest.mark.parametrize("body", [
    ["Python"], 42, None,
    {"skills": "Python"},
    {"skills": [" "]},
    {"skills": [{"name": "Python", "level": "secret"}]},
    {"skills": ["Python"], "top_k": "10"},
    {"skills": ["Python"], "top_k": True},
    {"skills": ["Python"], "enable_graph_hop": "false"},
    {"skills": ["Python"], "preferred_role": {"name": "Backend"}},
])
def test_bad_hybrid_input_never_reaches_retrieval(client_and_calls, body):
    client, calls = client_and_calls
    response = client.post("/api/match/hybrid-candidates", json=body)
    assert response.status_code == 400
    assert calls == []


def test_valid_hybrid_input_preserves_false_graph_switch(client_and_calls):
    client, calls = client_and_calls
    response = client.post("/api/match/hybrid-candidates", json={
        "skills": [" Python ", {"name": "SQL"}], "top_k": 3,
        "enable_graph_hop": False,
    })
    assert response.status_code == 200
    assert calls[0][1]["enable_graph_hop"] is False


def test_catalog_integrity_outage_is_not_an_empty_success(client_and_calls, monkeypatch):
    from app.api import match
    from app.services.hybrid_retrieve import CandidateCatalogUnavailable

    client, _ = client_and_calls
    def fail(*args, **kwargs):
        raise CandidateCatalogUnavailable("fixture catalog outage")
    monkeypatch.setattr(match, "hybrid_recommend", fail)
    response = client.post("/api/match/hybrid-candidates", json={"skills": ["Python"]})
    assert response.status_code == 503
    assert response.get_json()["status"] == "unavailable"
