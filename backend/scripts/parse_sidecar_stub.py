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
            return self._json(503, {
                "status": "unavailable",
                "engine": "stub",
                "ocr_pdf": False,
                "durable_tasks": False,
                "reason": "contract-only stub; no OCR engine is installed",
            })
        return self._json(404, {"error": "not found"})

    def do_POST(self):
        if not self.path.startswith("/api/cpa/parse"):
            return self._json(404, {"error": "not found"})
        return self._json(503, {"error": "contract stub cannot OCR PDF", "engine": "stub"})


def main():
    port = int(os.environ.get("PORT") or 9380)
    server = HTTPServer(("0.0.0.0", port), Handler)
    print(f"parse-sidecar stub on :{port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
