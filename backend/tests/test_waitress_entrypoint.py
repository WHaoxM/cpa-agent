import pytest

import serve_waitress


def test_bind_defaults_to_localhost(monkeypatch):
    monkeypatch.delenv("FLASK_HOST", raising=False)
    monkeypatch.delenv("FLASK_PORT", raising=False)

    assert serve_waitress.resolve_bind() == ("127.0.0.1", 5001)


def test_bind_accepts_ipv6_loopback(monkeypatch):
    monkeypatch.setenv("FLASK_HOST", "::1")
    monkeypatch.setenv("FLASK_PORT", "5001")

    assert serve_waitress.resolve_bind() == ("::1", 5001)


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.0.2.15", "example.com"])
def test_bind_rejects_non_loopback_hosts(monkeypatch, host):
    monkeypatch.setenv("FLASK_HOST", host)

    with pytest.raises(RuntimeError, match="loopback"):
        serve_waitress.resolve_bind()


@pytest.mark.parametrize("port", ["0", "65536", "not-a-port"])
def test_bind_rejects_invalid_ports(monkeypatch, port):
    monkeypatch.setenv("FLASK_HOST", "127.0.0.1")
    monkeypatch.setenv("FLASK_PORT", port)

    with pytest.raises(RuntimeError, match="FLASK_PORT"):
        serve_waitress.resolve_bind()


def test_entrypoint_uses_validated_app_and_waitress(monkeypatch):
    app = object()
    started = []
    monkeypatch.setenv("FLASK_HOST", "127.0.0.1")
    monkeypatch.setenv("FLASK_PORT", "5001")
    monkeypatch.setattr(serve_waitress, "create_production_app", lambda: app)
    monkeypatch.setattr(
        serve_waitress,
        "serve",
        lambda actual_app, **kwargs: started.append((actual_app, kwargs)),
    )

    serve_waitress.main()

    assert started == [(app, {"host": "127.0.0.1", "port": 5001})]


def test_fail_closed_app_factory_prevents_listener_start(monkeypatch):
    started = []
    monkeypatch.setenv("FLASK_HOST", "127.0.0.1")
    monkeypatch.setattr(
        serve_waitress,
        "create_production_app",
        lambda: (_ for _ in ()).throw(RuntimeError("schema mismatch")),
    )
    monkeypatch.setattr(serve_waitress, "serve", lambda *args, **kwargs: started.append(True))

    with pytest.raises(RuntimeError, match="schema mismatch"):
        serve_waitress.main()

    assert started == []
