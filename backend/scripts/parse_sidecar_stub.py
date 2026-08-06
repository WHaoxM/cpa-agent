"""Minimal HTTP stub implementing CPA parse-sidecar contract (dev/CI).

Endpoints:
  GET  /v1/health
  POST /api/cpa/parse  multipart file → { text, markdown, engine }
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def _json(self, code: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/v1/health"):
            return self._json(200, {"status": "ok", "engine": "stub"})
        return self._json(404, {"error": "not found"})

    def do_POST(self):
        if not self.path.startswith("/api/cpa/parse"):
            return self._json(404, {"error": "not found"})
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        # Stub: do not truly parse PDF; return placeholder so clients can wire contracts.
        name = "upload.pdf"
        if b'filename="' in raw:
            try:
                name = raw.split(b'filename="', 1)[1].split(b'"', 1)[0].decode("utf-8", "ignore")
            except Exception:
                pass
        text = (
            f"# Sidecar stub parse\n\n"
            f"file: {name}\n\n"
            f"Replace this stub with RAGFlow DeepDoc for production fidelity.\n"
        )
        return self._json(
            200,
            {
                "text": text,
                "markdown": text,
                "engine": "stub",
                "blocks": [{"type": "text", "text": text}],
            },
        )


def main():
    port = int(os.environ.get("PORT") or 9380)
    server = HTTPServer(("0.0.0.0", port), Handler)
    print(f"parse-sidecar stub on :{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
