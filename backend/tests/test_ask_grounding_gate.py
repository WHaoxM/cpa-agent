import json
from types import SimpleNamespace
import pytest

from backend.app.services.agent_runtime.quality_gate import ground_evidence_answer
from backend.app.services.agent_runtime.egress_pipeline import _public_progress


class Client:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.calls = []

    def chat_with_metadata(self, **kwargs):
        self.calls.append(kwargs)
        value = next(self.responses)
        if isinstance(value, Exception):
            raise value
        return SimpleNamespace(text=json.dumps(value), finish_reason="stop")


PACK = {"items": [{"data": {"title": "Experience"}}],
        "coverage": {"total": 6, "returned": 6, "complete": True}}
PASS = {"verdict": "supported", "reasons": [], "corrected_reply": ""}
FAIL = {"verdict": "unsupported", "reasons": ["Count does not establish ability"],
        "corrected_reply": "该章节有 6 条记录，数量不能证明实际贡献。"}


@pytest.fixture(autouse=True)
def opt_in(monkeypatch):
    monkeypatch.setenv("CPA_ASK_GROUNDING_REVIEW_ENABLED", "true")


def test_default_disabled_has_no_added_model_cost(monkeypatch):
    monkeypatch.delenv("CPA_ASK_GROUNDING_REVIEW_ENABLED")
    c = Client()
    assert ground_evidence_answer(c, "draft", "q", PACK)[1]["status"] == "not_checked"
    assert not c.calls


def test_no_personal_evidence_has_no_extra_call():
    c = Client()
    assert ground_evidence_answer(c, "你好", "你好", {})[1]["status"] == "not_applicable"
    assert not c.calls


def test_unsupported_revision_requires_second_check():
    c = Client(FAIL, PASS)
    answer, status = ground_evidence_answer(c, "工程能力优秀", "数量说明什么", PACK)
    assert answer == FAIL["corrected_reply"]
    assert status == {"status": "model_checked", "checks": 2, "revised": True}
    assert all(x["max_tokens"] == 1800 for x in c.calls)
    assert json.loads(c.calls[1]["messages"][1]["content"])["draft"] == answer


def test_second_failure_never_publishes_unverified_rewrite():
    c = Client(FAIL, FAIL)
    answer, status = ground_evidence_answer(c, "工程能力优秀", "数量说明什么", PACK)
    assert status["status"] == "blocked"
    assert "工程能力优秀" not in answer
    assert len(c.calls) == 2


def test_budget_or_schema_failure_fails_closed():
    for response in (RuntimeError("budget_exceeded"), {"verdict": "supported"}):
        answer, status = ground_evidence_answer(Client(response), "unsafe", "q", PACK)
        assert status["status"] == "blocked"
        assert "unsafe" not in answer


def test_truncated_review_never_approves_partial_json():
    c = Client()
    c.chat_with_metadata = lambda **kwargs: SimpleNamespace(text=json.dumps(PASS), finish_reason="length")
    answer, status = ground_evidence_answer(c, "unsafe", "q", PACK)
    assert status["status"] == "blocked"
    assert "unsafe" not in answer


def test_json_object_gate_keeps_local_pydantic_validation_and_default_is_unchanged():
    c = Client(PASS, PASS)
    answer, status = ground_evidence_answer(c, "draft", "q", PACK)
    assert answer == "draft"
    assert status["status"] == "model_checked"
    assert c.calls[0]["response_format"]["type"] == "json_schema"

    object_client = Client(PASS)
    answer, status = ground_evidence_answer(
        object_client, "draft", "q", PACK, response_format_kind="json_object",
        max_checks=1,
    )
    assert answer == "draft"
    assert status == {"status": "model_checked", "checks": 1, "revised": False}
    assert object_client.calls[0]["response_format"] == {"type": "json_object"}

    malformed = Client({"verdict": "supported", "reasons": [], "corrected_reply": "wrong"})
    answer, status = ground_evidence_answer(
        malformed, "draft", "q", PACK, response_format_kind="json_object",
        max_checks=1,
    )
    assert status["status"] == "blocked"
    assert "draft" not in answer


def test_single_check_diagnostic_names_initial_unsupported_and_never_returns_raw_review():
    c = Client(FAIL)
    answer, status = ground_evidence_answer(
        c, "draft", "q", PACK, response_format_kind="json_object",
        max_checks=1, include_diagnostics=True,
    )
    assert status["status"] == "blocked"
    assert status["checks"] == 1
    assert status["reason"] == "unsupported_initial_draft"
    assert status["review_verdict"] == "unsupported"
    assert status["review_reason_count"] == 1
    assert status["review_reason_chars"] == len(FAIL["reasons"][0])
    assert status["corrected_reply_chars"] == len(FAIL["corrected_reply"])
    assert "reasons" not in status
    assert "corrected_reply" not in status
    assert FAIL["reasons"][0] not in json.dumps(status)
    assert FAIL["corrected_reply"] not in answer
    non_diagnostic_client = Client(FAIL)
    ground_evidence_answer(
        non_diagnostic_client, "draft", "q", PACK,
        response_format_kind="json_object", max_checks=1,
    )
    assert c.calls[0]["messages"] == non_diagnostic_client.calls[0]["messages"]
    # The shared default two-check path retains its old public contract.
    _, default_status = ground_evidence_answer(Client(FAIL, FAIL), "draft", "q", PACK)
    assert default_status == {"status": "blocked", "checks": 2,
                              "reason": "unsupported_after_revision"}


def test_public_quality_diagnostics_do_not_leak_review_reasoning():
    assert _public_progress({"grounding": {"status": "blocked", "checks": 1,
        "reason": "review_unavailable", "private": "hidden"}}) == {
        "grounding": {"status": "blocked", "checks": 1, "reason": "review_unavailable"}}
