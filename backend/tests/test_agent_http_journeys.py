"""Outside-in, real HTTP journeys; deterministic Supervisor, not model quality."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest
import requests

SERVER = Path(__file__).with_name("fixtures") / "http_acceptance_server.py"
OWNER = "http_acceptance_owner"


@pytest.fixture
def http_backend(tmp_path):
    """A fresh process/socket/storage root per journey, even on assertion failure."""
    log = (tmp_path / "server.log").open("w", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, "-B", str(SERVER), "--state-dir", str(tmp_path)],
        cwd=SERVER.parents[3], stdout=log, stderr=subprocess.STDOUT,
        env={**os.environ, "PYTHONUTF8": "1"},
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    client = requests.Session()
    client.trust_env = False  # Never send loopback through a configured proxy.
    try:
        deadline = time.monotonic() + 40
        ready = tmp_path / "ready.json"
        while not ready.exists():
            if process.poll() is not None or time.monotonic() >= deadline:
                pytest.fail(f"HTTP backend failed to bind; {tmp_path / 'server.log'}\n"
                            + (tmp_path / "server.log").read_text(encoding="utf-8"))
            time.sleep(0.05)
        host = json.loads(ready.read_text(encoding="utf-8"))
        assert host["pid"] == process.pid and process.pid != os.getpid()
        transcript = tmp_path / "requests.jsonl"

        def request(method, path, *, owner=OWNER, **kwargs):
            headers = kwargs.pop("headers", {})
            if owner:
                headers["X-Dev-Student-Id"] = owner
            entry = {"method": method, "path": path, "owner": owner,
                     "json": kwargs.get("json"), "form": kwargs.get("data"),
                     "files": {key: {"name": value[0], "size": len(value[1]), "mime": value[2]}
                               for key, value in kwargs.get("files", {}).items()}}
            try:
                response = client.request(method, host["base_url"] + path,
                                          headers=headers, timeout=(3, 15), **kwargs)
            except requests.RequestException as exc:
                with transcript.open("a", encoding="utf-8") as output:
                    output.write(json.dumps({**entry, "error": str(exc)}, ensure_ascii=False) + "\n")
                raise
            with transcript.open("a", encoding="utf-8") as output:
                output.write(json.dumps({**entry,
                                         "status": response.status_code,
                                         "headers": dict(response.headers),
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


def data(response, status=200):
    assert response.status_code == status, response.text
    return response.json()["data"]


def state(root, sid):
    return json.loads((root / f"{sid}.state.json").read_text(encoding="utf-8"))


def calls(root):
    path = root / "supervisor-calls.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def sse(response):
    assert response.status_code == 200, response.text
    assert response.headers["Content-Type"].startswith("text/event-stream")
    lines = response.text.splitlines()
    assert lines[0] == ": connected"
    assert lines.count("data: [DONE]") == 1
    assert lines[-2:] == ["data: [DONE]", ""]
    events = [json.loads(line[6:]) for line in lines if line.startswith("data: {")]
    # Event name and id are verified separately from the JSON payload.
    for frame in response.text.split("\n\n"):
        if "event: " in frame:
            fields = dict(line.split(": ", 1) for line in frame.splitlines())
            event = json.loads(fields["data"])
            assert fields["event"] == event["type"]
            assert fields["id"] == str(event["event_id"])
    return events


def assert_turn(events, sid):
    assert events[0]["type"] == "turn_start"
    assert events[-1]["type"] == "turn_done"
    assert events[-1]["session_id"] == sid
    assert events[-1]["outcome"]["status"] == "completed"
    assert [event["event_id"] for event in events] == list(range(1, len(events) + 1))
    assert len({event["turn_id"] for event in events}) == 1
    return "".join(event["text"] for event in events if event["type"] == "reply_delta")


def test_http_multi_turn_preserves_session_history_and_context(http_backend):
    request, root = http_backend
    unauthenticated = request("POST", "/api/agent/session/create", owner=None, json={})
    assert unauthenticated.status_code == 401
    assert unauthenticated.json()["code"] == "AUTH_ERROR"
    sid = data(request("POST", "/api/agent/session/create", json={}))["session_id"]
    first = data(request("POST", "/api/agent/chat", json={
        "session_id": sid, "message": "I prefer Hangzhou", "interaction_mode": "ask"}))
    second_events = sse(request("POST", "/api/agent/chat/stream", json={
        "session_id": sid, "message": "Keep that city in the next step", "interaction_mode": "ask"}))
    assert assert_turn(first["events"], sid) == first["reply"]
    second_reply = assert_turn(second_events, sid)
    assert first["turn_id"] != second_events[0]["turn_id"]
    history = data(request("GET", f"/api/agent/session/{sid}/history"))
    assert history["status"] == "active"
    assert history["message_count"] == 4
    assert [m["role"] for m in history["messages"]] == ["user", "assistant"] * 2
    assert [m["content"] for m in history["messages"]] == [
        "I prefer Hangzhou", first["reply"], "Keep that city in the next step", second_reply]
    observed = calls(root)
    assert observed[0]["history"] == []
    assert [m["content"] for m in observed[1]["history"]] == ["I prefer Hangzhou", first["reply"]]
    assert observed[1]["session_context"]["blackboard"]["acceptance_turn_count"] == 1
    persisted = state(root, sid)
    assert persisted["context"]["blackboard"]["acceptance_turn_count"] == 2
    assert len(persisted["messages"]) == 4
    assert request("GET", f"/api/agent/session/{sid}/history", owner="other_owner").status_code == 403
    assert request("POST", "/api/agent/chat", owner="other_owner", json={
        "session_id": sid, "message": "must not mutate this session"}).status_code == 403
    assert state(root, sid) == persisted
    assert len(calls(root)) == 2


def test_http_upload_then_reference_orders_state_and_json_sse_semantics(http_backend):
    request, root = http_backend
    sid = data(request("POST", "/api/agent/session/create", json={}))["session_id"]
    uploaded = data(request("POST", "/api/agent/files", data={"session_id": sid},
                            files={"file": ("evidence.txt", b"ATTACHMENT_SENTINEL: Python project evidence.", "text/plain")}))
    fid = uploaded["file_id"]
    assert uploaded["status"] == "ready"
    assert data(request("GET", f"/api/agent/files/{fid}"))["status"] == "ready"
    assert state(root, sid)["messages"] == []
    assert calls(root) == []  # Upload is not a chat turn.
    assert data(request("GET", f"/api/agent/session/{sid}/resources"))["resources"] == []
    assert request("GET", f"/api/agent/files/{fid}", owner="other_owner").status_code == 403

    payload = {"session_id": sid, "message": "Use the attached evidence", "attachment_ids": [fid]}
    turn = data(request("POST", "/api/agent/chat", json=payload))
    assert assert_turn(turn["events"], sid) == turn["reply"]
    resources = data(request("GET", f"/api/agent/session/{sid}/resources"))["resources"]
    assert [r["file_id"] for r in resources] == [fid]
    assert resources[0]["session_id"] == sid
    disk_mounts = json.loads((root / "uploads" / "session_mounts" / f"{sid}.json").read_text(encoding="utf-8"))
    assert disk_mounts == resources
    file_meta = json.loads((root / "uploads" / "agent_files" / f"{fid}.meta.json").read_text(encoding="utf-8"))
    assert file_meta["status"] == "ready" and file_meta["student_id"] == OWNER
    observed = calls(root)[0]["prepared_turn"]
    assert observed["turn_file_ids"] == [fid] and not observed["mount_errors"]
    assert fid in observed["attach_card"]
    assert state(root, sid)["messages"][0]["metadata"]["attachment_ids"] == [fid]

    events = sse(request("POST", "/api/agent/chat/stream", json=payload))
    assert_turn(events, sid)
    assert [e["type"] for e in events] == [e["type"] for e in turn["events"]]
    assert events[-1]["outcome"] == turn["outcome"]
    assert data(request("GET", f"/api/agent/session/{sid}/resources"))["resources"] == resources
    before = state(root, sid)
    # One changed variable: missing file id; both transports must reject before STM/model.
    invalid = {**payload, "attachment_ids": ["file_missing_http_acceptance"]}
    rejected = request("POST", "/api/agent/chat", json=invalid)
    assert rejected.status_code == 404, rejected.text
    assert rejected.json()["code"] == "NOT_FOUND"
    errors = sse(request("POST", "/api/agent/chat/stream", json=invalid))
    assert [event["type"] for event in errors] == ["error"]
    assert errors[0]["code"] == "RESOURCE_NOT_FOUND"
    assert state(root, sid) == before and len(calls(root)) == 2
    history = data(request("GET", f"/api/agent/session/{sid}/history"))
    assert history["message_count"] == 4
    assert [m["role"] for m in history["messages"]] == ["user", "assistant"] * 2
