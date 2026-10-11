"""Real HTTP RAG acceptance: owner-scoped evidence, budget, continuation, egress."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest
import requests


SERVER = Path(__file__).with_name("fixtures") / "rag_http_acceptance_server.py"
OWNER = "rag_http_owner"
RESUME_URI = "career://me/resume"


@pytest.fixture
def rag_http(tmp_path):
    log = (tmp_path / "server.log").open("w", encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, "-B", str(SERVER), "--state-dir", str(tmp_path)],
        cwd=SERVER.parents[3],
        stdout=log,
        stderr=subprocess.STDOUT,
        env={**os.environ, "PYTHONUTF8": "1"},
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    client = requests.Session()
    client.trust_env = False
    try:
        deadline = time.monotonic() + 40
        ready_path = tmp_path / "ready.json"
        while not ready_path.exists():
            if process.poll() is not None or time.monotonic() >= deadline:
                pytest.fail(
                    f"RAG HTTP backend did not bind; {tmp_path / 'server.log'}\n"
                    + (tmp_path / "server.log").read_text(encoding="utf-8")
                )
            time.sleep(0.05)
        ready = json.loads(ready_path.read_text(encoding="utf-8"))
        assert ready["pid"] == process.pid and process.pid != os.getpid()

        def request(method, path, *, owner=OWNER, **kwargs):
            headers = dict(kwargs.pop("headers", {}) or {})
            if owner:
                headers["X-Dev-Student-Id"] = owner
            return client.request(
                method, ready["base_url"] + path,
                headers=headers, timeout=(3, 15), **kwargs,
            )

        health = request("GET", "/health", owner=None)
        assert health.status_code == 200 and health.json()["status"] == "ok"
        yield request, tmp_path, ready
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


def _data(response, status=200):
    assert response.status_code == status, response.text
    return response.json()["data"]


def _state(root, session_id):
    return json.loads((root / f"{session_id}.state.json").read_text(encoding="utf-8"))


def _llm_calls(root):
    path = root / "llm-calls.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def _sse(response):
    assert response.status_code == 200, response.text
    assert response.headers["Content-Type"].startswith("text/event-stream")
    frames = [frame for frame in response.text.split("\n\n") if frame]
    assert frames[0] == ": connected"
    assert frames[-1] == "data: [DONE]"
    events = []
    for frame in frames[1:-1]:
        lines = dict(line.split(": ", 1) for line in frame.splitlines() if ": " in line)
        event = json.loads(lines["data"])
        assert event["type"] == lines["event"]
        events.append(event)
    return events


def _public_evidence(turn_or_events):
    if isinstance(turn_or_events, dict):
        evidence = turn_or_events["orchestration"]["progress"]["evidence"]
        progress_events = [
            event for event in turn_or_events["events"] if event["type"] == "progress"
        ]
    else:
        progress_events = [event for event in turn_or_events if event["type"] == "progress"]
        evidence = progress_events[-1]["progress"]["evidence"]
    assert progress_events, "the API must project evidence coverage into its public event stream"
    return evidence


def _evidence_pack_from_call(call):
    joined = "\n".join(str(item.get("content") or "") for item in call["messages"])
    start_tag = '<SERVER_EVIDENCE kind="EvidencePack">\n'
    start = joined.find(start_tag)
    assert start >= 0, "the answer model call must receive the server EvidencePack"
    start += len(start_tag)
    end = joined.find("\n</SERVER_EVIDENCE>", start)
    assert end > start
    return json.loads(joined[start:end])


def test_http_owner_scoped_rag_budget_continues_and_persists_authoritative_evidence(rag_http):
    request, root, ready = rag_http
    assert "EvidencePack selection/budget/paging" in ready["real"]
    assert "LLM" in ready["substitutes"]

    session = _data(request("POST", "/api/agent/session/create", json={}))
    session_id = session["session_id"]
    first = _data(request("POST", "/api/agent/chat", json={
        "session_id": session_id,
        "interaction_mode": "ask",
        "message": "列出我已确认简历里的技能，并保留后续分页",
        "content_parts": [{"type": "resource_ref", "uri": RESUME_URI}],
    }))

    first_evidence = _public_evidence(first)
    assert first["outcome"]["status"] == "completed"
    assert first_evidence == {
        "status": "partial",
        "coverage": {"complete": False, "returned": 10, "total": 12, "reason": "item_limit"},
        "has_more": True,
        "next_offset": 10,
        "truncated": True,
        "next_action": "continue_evidence",
        "sources": [{
            "uri": RESUME_URI, "section": "skills", "version": "task_resume_rag_http",
            "status": "completed",
        }],
    }
    assert "10/12" in first["reply"] and "尚余 2 项" in first["reply"]
    first_calls = _llm_calls(root)
    answer_call = next(call for call in first_calls if call["stage"] == "answer")
    first_pack = _evidence_pack_from_call(answer_call)
    assert len(json.dumps(first_pack, ensure_ascii=False, separators=(",", ":"))) <= 4500
    assert first_pack["status"] == "partial"
    assert first_pack["coverage"]["returned"] == 10
    assert first_pack["has_more"] is True and first_pack["next_offset"] == 10
    assert [item["data"] for item in first_pack["items"]] == [f"RAG-SKILL-{i:02d}" for i in range(10)]
    assert first_calls[0]["stage"] == "evidence_selection"

    after_first = _state(root, session_id)
    cursor = after_first["context"]["ask_evidence_continuation"]
    assert cursor["student_id"] == OWNER and cursor["session_id"] == session_id
    assert cursor["cursor"]["next_offset"] == 10
    assert cursor["cursor"]["version"] == "task_resume_rag_http"

    second_events = _sse(request("POST", "/api/agent/chat/stream", json={
        "session_id": session_id,
        "interaction_mode": "ask",
        "message": "继续",
    }))
    second_evidence = _public_evidence(second_events)
    assert second_evidence["status"] == "completed"
    assert second_evidence["coverage"] == {
        "complete": True, "returned": 2, "total": 12, "reason": "complete",
    }
    assert second_evidence["has_more"] is False
    assert second_evidence["sources"] == [{
        "uri": RESUME_URI, "section": "skills", "version": "task_resume_rag_http",
        "status": "completed",
    }]
    second_calls = _llm_calls(root)
    second_answer = [call for call in second_calls if call["stage"] == "answer"][-1]
    second_pack = _evidence_pack_from_call(second_answer)
    assert [item["data"] for item in second_pack["items"]] == ["RAG-SKILL-10", "RAG-SKILL-11"]
    assert second_pack["coverage"]["complete"] is True and second_pack["has_more"] is False

    final_state = _state(root, session_id)
    assert final_state["context"]["ask_evidence_continuation"] is None
    history = _data(request("GET", f"/api/agent/session/{session_id}/history"))
    assert [message["role"] for message in history["messages"]] == ["user", "assistant", "user", "assistant"]
    first_assistant = history["messages"][1]
    second_assistant = history["messages"][3]
    assert first_assistant["metadata"]["orchestration"]["progress"]["evidence"]["has_more"] is True
    assert second_assistant["metadata"]["orchestration"]["progress"]["evidence"]["status"] == "completed"

    # One changed variable: an unsupported, unpartitioned public collection URI.
    before_public_refusal = _state(root, session_id)
    before_call_count = len(_llm_calls(root))
    public_refusal = request("POST", "/api/agent/chat", json={
        "session_id": session_id,
        "interaction_mode": "ask",
        "message": "search this public data",
        "content_parts": [{"type": "resource_ref", "uri": "career://public/skills"}],
    })
    assert public_refusal.status_code == 400, public_refusal.text
    assert _state(root, session_id) == before_public_refusal
    assert len(_llm_calls(root)) == before_call_count

    # A different owner cannot reinterpret `career://me/resume` as this user's evidence.
    other_session = _data(request(
        "POST", "/api/agent/session/create", owner="rag_http_other", json={},
    ))["session_id"]
    before_other = _state(root, other_session)
    before_call_count = len(_llm_calls(root))
    cross_owner = request("POST", "/api/agent/chat", owner="rag_http_other", json={
        "session_id": other_session,
        "interaction_mode": "ask",
        "message": "引用我的简历",
        "content_parts": [{"type": "resource_ref", "uri": RESUME_URI}],
    })
    assert cross_owner.status_code == 404, cross_owner.text
    assert _state(root, other_session) == before_other
    assert len(_llm_calls(root)) == before_call_count
