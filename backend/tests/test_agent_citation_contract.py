"""Citation parts must be safe and consistent across public projections."""

from contextlib import nullcontext
import json
from types import SimpleNamespace

from app.services.agent_runtime.egress_pipeline import project_session_messages, project_turn


def _citation(url):
    return {
        "type": "citation",
        "source": "简历第 2 页",
        "label": "内部标签",
        "quote": "教育经历",
        "snippet": "内部片段",
        "url": url,
        "page": 2,
        "section": "教育",
        "relevance": 0.9,
        "raw_args": {"secret": "do not publish"},
    }


def test_explicit_citation_drops_untrusted_url_and_extra_fields_in_json_sse_history():
    public = project_turn({
        "reply": "见引用。",
        "session_id": "s1",
        "parts": [_citation("javascript:alert(1)")],
    }).to_dict()
    citation = next(part for part in public["parts"] if part["type"] == "citation")
    assert citation["url"] is None
    assert set(citation) == {"type", "source", "quote", "page", "section", "url", "relevance"}
    assert "internal" not in str(citation)

    citation_events = [event for event in public["events"] if event["type"] == "citation"]
    assert len(citation_events) == 1
    assert citation_events[0]["citation"] == citation

    history = project_session_messages([{
        "role": "assistant",
        "content": public["reply"],
        "metadata": {"parts_v1": [_citation("javascript:alert(1)")]},
    }])
    assert history[0]["parts"][0] == citation
    assert history[0]["metadata"]["parts_v1"][0] == citation


def test_citation_keeps_valid_https_and_text_when_link_is_invalid():
    valid = project_turn({"parts": [_citation("https://example.org/source?q=1")]}).parts[0]
    assert valid["url"] == "https://example.org/source?q=1"
    assert valid["quote"] == "教育经历"

    for unsafe in ("data:text/html,hi", "file:///etc/passwd", "//example.org", "https:///missing-host", "https://user:pass@example.org", "java\nscript:alert(1)", "https://example.org\\@evil.org"):
        citation = project_turn({"parts": [_citation(unsafe)]}).parts[0]
        assert citation["url"] is None
        assert citation["quote"] == "教育经历"


def test_legacy_label_and_snippet_are_projected_without_extra_fields():
    citation = project_turn({"parts": [{
        "type": "citation",
        "label": "网页标题",
        "snippet": "摘要",
        "url": "https://example.org",
        "private_trace": "hidden",
    }]}).parts[0]
    assert citation["source"] == "网页标题"
    assert citation["quote"] == "摘要"
    assert citation["url"] == "https://example.org"
    assert set(citation) == {"type", "source", "quote", "page", "section", "url", "relevance"}


def test_evidence_egress_drops_unknown_status_reason_and_private_pack_content():
    private_prompt = "PRIVATE_PROMPT_SENTINEL"
    private_evidence = "PRIVATE_EVIDENCE_SENTINEL"
    public = project_turn({
        "reply": "已安全处理。",
        "session_id": "s1",
        "orchestration": {
            "supervisor_view": {
                "evidence_pack": {
                    "status": private_prompt,
                    "coverage": {
                        "complete": private_prompt,
                        "returned": private_evidence,
                        "total": -1,
                        "reason": private_evidence,
                    },
                    "items": [{"data": private_evidence}],
                    "has_more": False,
                    "next_offset": None,
                    "truncated": False,
                },
            },
        },
    }).to_dict()

    evidence = public["orchestration"]["progress"]["evidence"]
    assert evidence["status"] == "unavailable"
    assert evidence["coverage"] == {}
    serialized_turn = json.dumps(public, ensure_ascii=False)
    assert private_prompt not in serialized_turn
    assert private_evidence not in serialized_turn

    # Session history reprojects the already-public summary; it must not
    # reintroduce the original evidence fields or sentinel values.
    history = project_session_messages([{
        "role": "assistant",
        "content": public["reply"],
        "metadata": {"orchestration": public["orchestration"]},
    }])
    serialized_history = json.dumps(history, ensure_ascii=False)
    assert private_prompt not in serialized_history
    assert private_evidence not in serialized_history
    assert history[0]["metadata"]["orchestration"]["progress"]["evidence"]["status"] == "unavailable"


def test_actual_chat_json_and_sse_routes_use_the_same_safe_citation(monkeypatch):
    from app import create_app
    from app.api import agent as agent_api
    from app.config import Config

    monkeypatch.setattr(Config, "DEMO_MODE", True)
    monkeypatch.setattr(Config, "DEMO_STUDENT_ID", "demo_student")
    monkeypatch.setattr(Config, "DEMO_PRINCIPAL_ID", "demo:demo_student")

    class SessionManagerStub:
        def __init__(self):
            self.messages = []

        def add_message(self, **kwargs):
            self.messages.append(kwargs)

    session_mgr = SessionManagerStub()
    monkeypatch.setattr(agent_api, "get_session_manager", lambda: session_mgr)
    monkeypatch.setattr(agent_api, "_resolve_session", lambda *_args: ("s1", SimpleNamespace(student_id="demo_student")))
    monkeypatch.setattr(agent_api, "get_session_coordinator", lambda: SimpleNamespace(lock=lambda _sid: nullcontext()))
    monkeypatch.setattr(
        "app.services.agent_runtime.media.prepare_turn.prepare_user_turn",
        lambda text, **_kwargs: {"visible_text": text, "turn_file_ids": [], "parts": [], "mount_errors": []},
    )
    raw = {"reply": "见引用。", "session_id": "s1", "parts": [_citation("javascript:alert(1)")]}
    monkeypatch.setattr(agent_api, "_run_supervisor", lambda *_args, **_kwargs: (project_turn(raw).to_dict(), raw))

    client = create_app().test_client()
    sync = client.post("/api/agent/chat", json={"message": "请给来源"})
    assert sync.status_code == 200
    citation = sync.get_json()["data"]["parts"][0]
    assert citation["url"] is None

    stream = client.post("/api/agent/chat/stream", json={"message": "请给来源"})
    assert stream.status_code == 200
    events = []
    for line in stream.get_data(as_text=True).splitlines():
        if line.startswith("data: {"):
            events.append(json.loads(line.removeprefix("data: ")))
    citation_events = [event for event in events if event["type"] == "citation"]
    assert len(citation_events) == 1
    assert citation_events[0]["citation"] == citation
