"""Isolated contract tests for an unreleased typed education extractor."""

import hashlib
import json
from types import SimpleNamespace

import pytest

from app.services.agent_runtime.budget import (
    ContextWindowStatus,
    capture_usage_ledger,
    record_model_call,
    require_turn_token_capacity,
)
from app.services.agent_runtime.education_screening import (
    EducationConditionEnvelope,
    ReadOnlySourceReceipt,
    extract_education_conditions,
)


SKILL_SHA256 = "a" * 64
USAGE = {"prompt_tokens": 42, "completion_tokens": 18, "total_tokens": 60}


def _condition(source_quote, *, source_id="user_message", **updates):
    row = {
        "source_id": source_id,
        "source_quote": source_quote,
        "speaker_candidate": "user_provided_notice",
        "dimension_candidate": "education_level",
        "condition_type_candidate": "must",
        "operator_candidate": "minimum",
        "relation_candidate": "none",
        "value_quote": "硕士及以上",
    }
    row.update(updates)
    return row


def _status(provider="openai", model="gpt-4o-mini"):
    return ContextWindowStatus(
        provider=provider, model=model, context_window_tokens=8192,
        input_limit_tokens=7000, estimated_input_tokens=60,
        remaining_input_tokens=6940, requested_output_tokens=1200,
        fits=True, estimate_method="test_fixture", reason="fits",
    )


class _FakeLLM:
    def __init__(self, payload, *, usage=USAGE, finish_reason="stop", record=True):
        self.payload = payload
        self.usage = usage
        self.finish_reason = finish_reason
        self.record = record
        self.calls = []

    def chat_with_metadata(self, **kwargs):
        self.calls.append(kwargs)
        if self.record:
            record_model_call(
                provider="openai", model="gpt-4o-mini", stage="llm_client.chat",
                usage=self.usage, preflight=_status(), finish_reason=self.finish_reason,
            )
        text = self.payload if isinstance(self.payload, str) else json.dumps(
            self.payload, ensure_ascii=False,
        )
        return SimpleNamespace(
            text=text, provider="openai", model="gpt-4o-mini",
            usage=self.usage, finish_reason=self.finish_reason,
        )


def _request(
    llm, message="岗位原文：硕士及以上，计算机相关专业优先。",
    *, include_redacted_diagnostics=False,
):
    return extract_education_conditions(
        user_message=message,
        owner_id="owner_test",
        run_id="run_test",
        skill_sha256=SKILL_SHA256,
        llm=llm,
        include_redacted_diagnostics=include_redacted_diagnostics,
    )


def test_typed_extractor_returns_only_unverified_candidates_with_replay_metadata():
    message = "岗位原文：硕士及以上，计算机相关专业优先。"
    fake = _FakeLLM({"schema_version": 1, "conditions": [
        _condition("硕士及以上", value_quote="硕士及以上"),
    ]})
    with capture_usage_ledger("agent_run_step", turn_token_cap=4000) as ledger:
        record = _request(fake, message)

    assert record["status"] == "model_candidate_unverified"
    assert record["error_code"] is None
    assert record["conditions"][0]["interpretation_status"] == "model_candidate_unverified"
    assert record["conditions"][0]["source_kind"] == "user_provided_unverified"
    assert record["conditions"][0]["quote_integrity"] == "exact_substring_only"
    assert record["input_message_sha256"] == hashlib.sha256(message.encode("utf-8")).hexdigest()
    assert len(record["prompt_sha256"]) == 64
    assert len(record["schema_sha256"]) == 64
    assert record["skill"] == {
        "skill_id": "education-screening-context",
        "sha256": SKILL_SHA256,
        "body_in_extractor_prompt": False,
        "purpose": "reproducibility_reference_only",
    }
    assert record["provider"] == "openai"
    assert record["model"] == "gpt-4o-mini"
    assert record["finish_reason"] == "stop"
    assert record["usage"] == USAGE
    assert record["usage_status"] == "observed"
    assert len(ledger["calls"]) == 1
    assert fake.calls[0]["response_format"] == {"type": "json_object"}
    assert "硕士及以上" in fake.calls[0]["messages"][1]["content"]
    assert "QA" not in fake.calls[0]["messages"][1]["content"]
    assert record["source_scope"] == "exact_quote_presence_only_not_source_authenticity_or_completeness"


def test_extractor_requires_run_step_budget_before_any_provider_call():
    fake = _FakeLLM({"schema_version": 1, "conditions": []})
    record = _request(fake)

    assert fake.calls == []
    assert record["status"] == "unavailable"
    assert record["error_code"] == "budget_context_required"
    assert record["usage_status"] == "unverified"


@pytest.mark.parametrize("bad_payload,expected_path", [
    ({"schema_version": 1, "conditions": [], "unexpected": True}, "unexpected"),
    ({"schema_version": "1", "conditions": []}, "schema_version"),
    ({"schema_version": 1, "conditions": [{"source_id": "user_message"}]}, "conditions"),
])
def test_typed_schema_rejects_extra_or_wrongly_typed_fields_without_raw_echo(bad_payload, expected_path):
    fake = _FakeLLM(bad_payload)
    with capture_usage_ledger("agent_run_step", turn_token_cap=4000):
        record = _request(fake)

    assert record["status"] == "invalid"
    assert record["error_code"] == "typed_schema_invalid"
    assert any(expected_path in error["path"] for error in record["validation_failures"])
    assert "unexpected" not in str(record.get("conditions"))


@pytest.mark.parametrize("row,expected", [
    (_condition("不存在的学历条件"), "quote_not_exact_source_substring"),
    (_condition("硕士及以上", source_id="injected_receipt"), "unknown_source_id"),
    (_condition("硕士及以上", value_quote="博士"), "value_not_in_exact_quote"),
])
def test_domain_validation_binds_source_ids_quotes_and_values(row, expected):
    fake = _FakeLLM({"schema_version": 1, "conditions": [row]})
    with capture_usage_ledger("agent_run_step", turn_token_cap=4000):
        record = _request(fake)

    assert record["status"] == "invalid"
    assert record["error_code"] == "source_or_domain_validation_failed"
    assert expected in {failure["code"] for failure in record["validation_failures"]}
    assert record["conditions"] == []


def test_opt_in_row_diagnostics_are_redacted_and_do_not_change_provider_request():
    project_marker = "候选项目代号-极昼-PRIVATE-9371"
    message = f"岗位原文：硕士及以上，计算机相关专业优先。候选人项目：{project_marker}。"
    quote = message
    payload = {"schema_version": 1, "conditions": [
        _condition(quote, value_quote=project_marker, relation_candidate="and"),
    ]}
    diagnostic_llm = _FakeLLM(payload)
    baseline_llm = _FakeLLM(payload)
    with capture_usage_ledger("agent_run_step", turn_token_cap=4000):
        diagnostic_record = _request(
            diagnostic_llm, message, include_redacted_diagnostics=True,
        )
    with capture_usage_ledger("agent_run_step", turn_token_cap=4000):
        baseline_record = _request(baseline_llm, message)

    assert diagnostic_record["status"] == "partial"
    assert diagnostic_record["error_code"] == "downgraded_unproven_relation"
    assert diagnostic_record["conditions"][0]["relation_candidate"] == "unknown"
    assert diagnostic_record["conditions"][0]["qualification_use"] == "not_permitted"
    assert diagnostic_record["conditions"][0]["diagnostics"] == [
        "downgraded_unproven_relation",
    ]
    assert diagnostic_record["input_message_sha256"] == hashlib.sha256(
        message.encode("utf-8"),
    ).hexdigest()
    assert diagnostic_record["redacted_row_diagnostics"] == [{
        "index": 0,
        "source_id": "user_message",
        "operator_enum": "minimum",
        "relation_enum": "and",
        "quote_length_chars": len(quote),
        "marker_presence": {
            "quote": {
                "minimum": True, "equals": False, "one_of": False,
                "all_of": False, "exception": False, "branch_any": False,
            },
            "source_sentence": {
                "minimum": True, "equals": False, "one_of": False,
                "all_of": False, "exception": False, "branch_any": False,
            },
        },
        "lexical_guard": {
            "operator_expected_marker_in_quote": True,
            "relation_expected_marker_in_quote": False,
            "combined_guard_pass": False,
        },
        "validation_code": "downgraded_unproven_relation",
    }]
    diagnostic_json = json.dumps(
        diagnostic_record["redacted_row_diagnostics"],
        ensure_ascii=False,
        sort_keys=True,
    )
    assert message not in diagnostic_json
    assert project_marker not in diagnostic_json
    assert quote not in diagnostic_json
    assert "硕士及以上" not in diagnostic_json
    assert "source_quote" not in diagnostic_record["redacted_row_diagnostics"][0]
    assert "value_quote" not in diagnostic_record["redacted_row_diagnostics"][0]
    assert "redacted_row_diagnostics" not in baseline_record

    # Diagnostics are computed after parsing; no provider-facing token changes.
    assert diagnostic_llm.calls[0] == baseline_llm.calls[0]
    assert diagnostic_record["prompt_sha256"] == baseline_record["prompt_sha256"]
    assert diagnostic_record["schema_sha256"] == baseline_record["schema_sha256"]
    assert diagnostic_record["request_sources_sha256"] == baseline_record[
        "request_sources_sha256"
    ]


@pytest.mark.parametrize(("operator", "quote", "expected_field"), [
    ("one_of", "计算机、自动化专业", "operator_candidate"),
    ("equals", "学历为本科", "operator_candidate"),
    ("equals", "专业不限", "operator_candidate"),
])
def test_unproven_operator_is_downgraded_without_using_it_for_eligibility(
    operator, quote, expected_field,
):
    fake = _FakeLLM({"schema_version": 1, "conditions": [
        _condition(
            quote,
            operator_candidate=operator,
            relation_candidate="unknown",
            value_quote=quote,
        ),
    ]})
    with capture_usage_ledger("agent_run_step", turn_token_cap=4000):
        record = _request(fake, f"岗位原文：{quote}。")

    assert record["status"] == "partial"
    assert record["error_code"] == "downgraded_unproven_relation"
    assert record["validation_failures"] == []
    candidate = record["conditions"][0]
    assert candidate[expected_field] == "unknown"
    assert candidate["downgraded_fields"] == [expected_field]
    assert candidate["diagnostics"] == ["downgraded_unproven_relation"]
    assert candidate["qualification_use"] == "not_permitted"
    assert candidate["interpretation_status"] == "model_candidate_unverified"


def test_explicit_or_and_exception_markers_remain_preserved_not_downgraded():
    message = "公告要求本科或硕士；退役士兵可放宽届别。"
    # Keep the adjacent exception in each quote: the source-integrity guard
    # intentionally rejects a quote that clips a branch from the same sentence.
    or_quote = message[:-1]
    exception_quote = message[:-1]
    fake = _FakeLLM({"schema_version": 1, "conditions": [
        _condition(
            or_quote,
            operator_candidate="one_of",
            relation_candidate="or",
            value_quote="本科或硕士",
        ),
        _condition(
            exception_quote,
            dimension_candidate="graduation_cohort",
            condition_type_candidate="exception",
            operator_candidate="except",
            relation_candidate="exception",
            value_quote="届别",
        ),
    ]})
    with capture_usage_ledger("agent_run_step", turn_token_cap=4000):
        record = _request(fake, message)

    assert record["status"] == "model_candidate_unverified"
    assert record["error_code"] is None
    assert len(record["conditions"]) == 2
    assert [row["operator_candidate"] for row in record["conditions"]] == [
        "one_of", "except",
    ]
    assert [row["relation_candidate"] for row in record["conditions"]] == [
        "or", "exception",
    ]
    assert all(row["downgraded_fields"] == [] for row in record["conditions"])
    assert all(row["qualification_use"] == "not_permitted" for row in record["conditions"])


def test_mixed_rows_keep_valid_and_downgraded_candidates_but_reject_fabricated_quote():
    message = "公告要求本科或硕士；退役士兵可放宽届别。硕士及以上。学历为本科。"
    valid_quote = "硕士及以上"
    downgraded_quote = "学历为本科"
    fabricated_quote = "博士及以上"
    fake = _FakeLLM({"schema_version": 1, "conditions": [
        _condition(valid_quote, value_quote=valid_quote),
        _condition(
            downgraded_quote,
            operator_candidate="equals",
            relation_candidate="unknown",
            value_quote="本科",
        ),
        _condition(fabricated_quote, value_quote=fabricated_quote),
    ]})
    with capture_usage_ledger("agent_run_step", turn_token_cap=4000):
        record = _request(fake, message, include_redacted_diagnostics=True)

    assert record["status"] == "partial"
    assert record["error_code"] == "source_or_domain_validation_partial"
    assert len(record["conditions"]) == 2
    assert record["conditions"][0]["operator_candidate"] == "minimum"
    assert record["conditions"][0]["qualification_use"] == "not_permitted"
    assert record["conditions"][1]["operator_candidate"] == "unknown"
    assert record["conditions"][1]["qualification_use"] == "not_permitted"
    assert "quote_not_exact_source_substring" in {
        failure["code"] for failure in record["validation_failures"]
    }
    assert [row["validation_code"] for row in record["redacted_row_diagnostics"]] == [
        "accepted", "downgraded_unproven_relation", "quote_not_exact_source_substring",
    ]
    assert fabricated_quote not in json.dumps(record["redacted_row_diagnostics"])


def test_quote_that_drops_or_or_exception_marker_is_rejected():
    message = "公告要求本科或硕士，退役士兵可放宽届别。"
    fake = _FakeLLM({"schema_version": 1, "conditions": [
        _condition("公告要求本科", value_quote="本科"),
    ]})
    with capture_usage_ledger("agent_run_step", turn_token_cap=4000):
        record = _request(fake, message)

    assert record["status"] == "invalid"
    assert "quote_may_omit_branch_or_exception" in {
        failure["code"] for failure in record["validation_failures"]
    }


def test_server_readonly_receipt_must_be_owner_run_bound_and_hash_fixed():
    source_text = "岗位公告原文：硕士及以上。"
    receipt = ReadOnlySourceReceipt(
        source_id="notice:42",
        owner_id="owner_test",
        run_id="run_test",
        tool_name="career_data_read",
        read_status="completed",
        read_only=True,
        source_version="snapshot-2026-10-19",
        source_sha256=hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
        text=source_text,
    )
    fake = _FakeLLM({"schema_version": 1, "conditions": [
        _condition("硕士及以上", source_id="notice:42", value_quote="硕士及以上"),
    ]})
    with capture_usage_ledger("agent_run_step", turn_token_cap=4000):
        record = extract_education_conditions(
            user_message="请按该公告分析。", owner_id="owner_test", run_id="run_test",
            skill_sha256=SKILL_SHA256, receipts=[receipt], llm=fake,
        )
    assert record["status"] == "model_candidate_unverified"
    assert record["conditions"][0]["source_kind"] == "server_readonly_receipt"
    assert record["conditions"][0]["source_version"] == "snapshot-2026-10-19"
    assert record["conditions"][0]["source_sha256"] == receipt.source_sha256
    assert record["source_manifest"][1]["read_status"] == "completed"

    wrong_owner = ReadOnlySourceReceipt(
        **{**receipt.__dict__, "owner_id": "someone_else"},
    )
    never_called = _FakeLLM({"schema_version": 1, "conditions": []})
    with capture_usage_ledger("agent_run_step", turn_token_cap=4000):
        invalid = extract_education_conditions(
            user_message="请按该公告分析。", owner_id="owner_test", run_id="run_test",
            skill_sha256=SKILL_SHA256, receipts=[wrong_owner], llm=never_called,
        )
    assert invalid["error_code"] == "source_receipt_owner_run_mismatch"
    assert never_called.calls == []


def test_extractor_missing_usage_cannot_become_zero_or_candidate():
    fake = _FakeLLM(
        {"schema_version": 1, "conditions": [_condition("硕士及以上")]},
        usage=None,
    )
    with capture_usage_ledger("agent_run_step", turn_token_cap=4000) as ledger:
        record = _request(fake)

    assert len(ledger["calls"]) == 1
    assert ledger["calls"][0]["usage"] is None
    assert record["status"] == "unavailable"
    assert record["error_code"] == "provider_usage_missing"
    assert record["usage_status"] == "unverified"
    assert record["usage"] is None
    assert record["conditions"] == []


def test_extractor_refuses_unrecorded_model_call_even_if_shape_looks_valid():
    fake = _FakeLLM({"schema_version": 1, "conditions": []}, record=False)
    with capture_usage_ledger("agent_run_step", turn_token_cap=4000):
        record = _request(fake)

    assert record["status"] == "unavailable"
    assert record["error_code"] == "provider_usage_not_recorded"
    assert record["conditions"] == []


def test_extractor_release_mismatch_fails_closed_before_model_dispatch():
    fake = _FakeLLM({"schema_version": 1, "conditions": []})
    with capture_usage_ledger("agent_run_step", turn_token_cap=4000):
        record = extract_education_conditions(
            user_message="岗位公告：硕士及以上。", owner_id="owner_test",
            run_id="run_test", skill_sha256=SKILL_SHA256, llm=fake,
            expected_runtime_contract={"contract_version": 1},
        )

    assert fake.calls == []
    assert record["status"] == "unavailable"
    assert record["error_code"] == "runtime_release_changed"
    assert record["conditions"] == []


def test_shared_step_budget_blocks_extractor_dispatch_after_qa_consumes_cap():
    message = "岗位公告：硕士及以上。"
    fake_dispatches = []

    class BudgetAwareFake:
        def chat_with_metadata(self, **_kwargs):
            require_turn_token_capacity(120, estimated_input_tokens=10)
            fake_dispatches.append(1)
            raise AssertionError("cap must stop before dispatch")

    with capture_usage_ledger("agent_run_step", turn_token_cap=250) as ledger:
        record_model_call(
            provider="openai", model="gpt-4o-mini", stage="qa",
            usage={"prompt_tokens": 120, "completion_tokens": 80, "total_tokens": 200},
            preflight=_status(), finish_reason="stop",
        )
        result = extract_education_conditions(
            user_message=message, owner_id="owner_test", run_id="run_test",
            skill_sha256=SKILL_SHA256, llm=BudgetAwareFake(),
        )

    assert fake_dispatches == []
    assert result["status"] == "unavailable"
    assert result["error_code"] == "turn_token_cap_exceeded"
    assert len(ledger["calls"]) == 1
    assert result["usage"] is None


def test_non_stop_json_response_is_not_exposed_as_candidate():
    fake = _FakeLLM(
        {"schema_version": 1, "conditions": [_condition("硕士及以上")]},
        finish_reason="length",
    )
    with capture_usage_ledger("agent_run_step", turn_token_cap=4000):
        record = _request(fake)

    assert record["status"] == "invalid"
    assert record["error_code"] == "json_response_not_complete"
    assert record["conditions"] == []


def test_envelope_schema_is_closed_and_requires_fields():
    with pytest.raises(Exception):
        EducationConditionEnvelope.model_validate(
            {"schema_version": 1, "conditions": [], "qa_text": "ignored"},
            strict=True,
        )
