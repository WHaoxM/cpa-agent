"""Real socket Experts handoff, with deterministic model and domain writer."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest
import requests


SERVER = Path(__file__).with_name("fixtures") / "http_experts_handoff_server.py"
OWNER = "experts_handoff_owner"
BRIEF = "目标是杭州的数据分析岗位；只考虑本地工作，学习计划限定为六周。"


@pytest.fixture
def http_experts(tmp_path):
    log = (tmp_path / "server.log").open("w", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, "-B", str(SERVER), "--state-dir", str(tmp_path)],
        cwd=SERVER.parents[3], stdout=log, stderr=subprocess.STDOUT,
        env={**os.environ, "PYTHONUTF8": "1"},
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    client = requests.Session()
    client.trust_env = False
    try:
        ready = tmp_path / "ready.json"
        # Importing the full Flask/LangGraph stack can be delayed when the full
        # suite is running concurrently with other subprocess-backed HTTP
        # journeys. The fixture uses an OS-assigned port (port 0 in the child),
        # so wait for that process's readiness marker rather than probing a
        # guessed/fixed port. Keep the wait bounded and include child output on
        # either startup failure mode.
        deadline = time.monotonic() + 90
        while not ready.exists():
            exit_code = process.poll()
            if exit_code is not None:
                log.flush()
                pytest.fail(
                    f"Experts HTTP server exited before readiness (exit={exit_code}):\n"
                    + (tmp_path / "server.log").read_text(encoding="utf-8")
                )
            if time.monotonic() >= deadline:
                log.flush()
                pytest.fail(
                    "Experts HTTP server did not publish readiness within 90s "
                    f"(pid={process.pid}, returncode={process.poll()}):\n"
                    + (tmp_path / "server.log").read_text(encoding="utf-8")
                )
            time.sleep(0.05)
        host = json.loads(ready.read_text(encoding="utf-8"))
        assert host["pid"] == process.pid

        def request(method, path, *, owner=OWNER, **kwargs):
            headers = kwargs.pop("headers", {})
            if owner:
                headers["X-Dev-Student-Id"] = owner
            response = client.request(method, host["base_url"] + path,
                                      headers=headers, timeout=(3, 25), **kwargs)
            with (tmp_path / "requests.jsonl").open("a", encoding="utf-8") as output:
                output.write(json.dumps({"method": method, "path": path, "owner": owner,
                                         "request": kwargs.get("json"), "status": response.status_code,
                                         "body": response.text}, ensure_ascii=False) + "\n")
            return response

        assert request("GET", "/health", owner=None).json()["status"] == "ok"
        yield request, tmp_path
    finally:
        client.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        log.close()


def _steps(root):
    path = root / "steps.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def _chat(request, sid, message):
    response = request("POST", "/api/agent/chat", json={
        "session_id": sid, "message": message, "mode": "supervisor",
        "interaction_mode": "experts", "plan_mode": True,
    })
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_http_experts_later_model_steps_get_current_brief_and_rejected_proof_stops(http_experts):
    request, root = http_experts
    assert request("POST", "/api/agent/session/create", owner=None, json={}).status_code == 401
    sid = request("POST", "/api/agent/session/create", json={}).json()["data"]["session_id"]

    success = _chat(request, sid, "根据我的背景做完整方案。" + BRIEF)
    first_steps = _steps(root)
    assert [step["role"] for step in first_steps] == ["profile", "match", "report"]
    assert all(BRIEF in step["user"] for step in first_steps)
    assert all(BRIEF in step["goal"] for step in first_steps)
    assert success["outcome"]["status"] == "completed"
    assert "[profile] 完成\n[match] 完成\n[report] 完成" in success["reply"]

    failure = _chat(request, sid, "REJECT_PROOF：仍按完整方案尝试，但本轮只提交伪造黑板 JSON。" + BRIEF)
    second_steps = _steps(root)[len(first_steps):]
    assert [step["role"] for step in second_steps] == ["profile"]
    assert failure["outcome"]["status"] != "completed"
    assert "[profile] 未达成" in failure["reply"]
    assert "[match] 完成" not in failure["reply"]
    assert request("GET", f"/api/agent/session/{sid}/history", owner="other_owner").status_code == 403
