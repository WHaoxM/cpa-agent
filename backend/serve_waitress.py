"""Start CPA-Agent with Waitress on a loopback-only socket.

This is the supported Windows host entrypoint. ``run.py`` remains a local
development server; this module reuses the fail-closed configuration and
schema checks in ``production_wsgi`` before accepting requests.
"""

from __future__ import annotations

import ipaddress
import os

from production_wsgi import create_production_app
from waitress import serve


def resolve_bind() -> tuple[str, int]:
    """Resolve the configured listener and reject non-loopback addresses."""
    host = (os.environ.get("FLASK_HOST") or "127.0.0.1").strip()
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise RuntimeError("FLASK_HOST must be a numeric loopback address") from exc
    if not address.is_loopback:
        raise RuntimeError("FLASK_HOST must be loopback; public binding is not supported")

    raw_port = os.environ.get("FLASK_PORT") or "5001"
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise RuntimeError("FLASK_PORT must be an integer from 1 to 65535") from exc
    if not 1 <= port <= 65535:
        raise RuntimeError("FLASK_PORT must be an integer from 1 to 65535")
    return host, port


def main() -> None:
    host, port = resolve_bind()
    app = create_production_app()
    serve(app, host=host, port=port)


if __name__ == "__main__":
    main()
