import copy
import hashlib
import json
from types import SimpleNamespace

import pytest

from app.pipeline.entity_extractor.portrait_generator import PortraitGenerator


SOURCE = "省属公办普通本科高校。组织讨论并与同伴完成系统交付。"
SOURCES = [{"source_id": "resume", "source_kind": "resume", "page": 2, "block_id": "b1", "text": SOURCE}]


def reference(sources=None, source_text=SOURCE):
    bank = PortraitGenerator()._assessment_evidence_bank(source_text, sources or SOURCES)
    return {"evidence_id": next(iter(bank))}


def proposal():
    item = {"level": "中", "confidence": 0.7, "rationale": "有行动与结果，持续性尚待补充。",
            "evidence": [reference()], "situation": None, "action": reference(), "outcome": reference()}
    return {"school_type": {"value": "省属公办普通本科高校", "evidence": [reference()]},
        "communication": copy.deepcopy(item), "teamwork": copy.deepcopy(item),
        "responsibility": copy.deepcopy(item),
        "stress_tolerance": {"level": None, "confidence": 0.0, "rationale": "缺压力情境与应对证据。",
                             "evidence": [], "situation": None, "action": None, "outcome": None}}


class Client:
    def __init__(self, data, finish_reason="stop"):
        self.data, self.finish_reason = data, finish_reason

    def chat_with_metadata(self, **kwargs):
        assert kwargs["response_format"]["json_schema"]["strict"] is True
        self.request = kwargs
        return SimpleNamespace(text=json.dumps(self.data, ensure_ascii=False),
                               provider="test-provider", model="test-model", finish_reason=self.finish_reason)


def candidate(data=None):
    return PortraitGenerator().propose_behavior_assessment(Client(data or proposal()), source_text=SOURCE,
        sources=SOURCES)


def profile():
    return {"degree": "本科", "source_sha256": hashlib.sha256(SOURCE.encode()).hexdigest(),
            "_resume_confirmation": {"task_id": "confirmed-source"}, "portrait_assessment": candidate()}


def approve(value):
    value["_portrait_assessment_confirmation"] = {
        "source_task_id": "confirmed-source", "candidate_sha256": value["portrait_assessment"]["candidate_sha256"],
        "confirmed_by": "trusted-owner", "confirmed_at": "2026-10-10T00:00:00Z"}


def test_school_type_is_not_admission_tier_or_score():
    value = profile()
    approve(value)
    result = PortraitGenerator()._calc_d1_basic([], value)
    assert result["school_type"]["value"] == "省属公办普通本科高校"
    assert result["school_type_status"] == "reviewed_observation"
    assert result["school_tier"] is None
    assert result["sub_school"] is None
    assert result["school_score_basis"] == "no_tier_mapping"


def test_candidate_and_old_resume_confirmation_never_score():
    result = PortraitGenerator()._calc_d3_quality([], profile())
    assert result["communication"]["score"] is None
    assert result["communication"]["assessment_candidate"]["level"] == "中"
    assert result["communication"]["assessment_status"] == "awaiting_confirmation"
    assert result["dimension_score"] is None


def test_reviewed_assessment_has_distinct_provenance_and_unknown_stress():
    value = profile()
    approve(value)
    result = PortraitGenerator().generate_student_portrait("owner", [], student_profile=value)
    comm = result["D3_quality"]["communication"]
    assert comm["score"] == 12
    assert comm["provenance"]["kind"] == "model_assessment"
    assert comm["confidence"] == 0.7
    assert result["D3_quality"]["stress_tolerance"]["score"] is None
    assert result["status"] == "partial"
    assert result["summary"]["competitiveness_score"] is None


@pytest.mark.parametrize("tamper", ["candidate", "source", "rubric", "approval", "source_task"])
def test_stale_or_tampered_assessment_is_not_scored(tamper):
    value = profile()
    approve(value)
    if tamper == "candidate":
        value["portrait_assessment"]["assessment"]["communication"]["level"] = "强"
    elif tamper == "source":
        value["source_sha256"] = "a" * 64
    elif tamper == "rubric":
        value["portrait_assessment"]["provenance"]["rubric_version"] = "old"
    elif tamper == "approval":
        value["_portrait_assessment_confirmation"]["candidate_sha256"] = "b" * 64
    else:
        value["_resume_confirmation"]["task_id"] = "different-source-task"
    assert PortraitGenerator()._calc_d3_quality([], value)["communication"]["score"] is None


@pytest.mark.parametrize("tamper", ["quote", "page", "confidence", "stress", "fake_confirmation", "length"])
def test_invalid_provider_result_is_explicit_partial(tamper):
    data = proposal()
    if tamper == "quote":
        data["communication"]["evidence"] = [{**reference(), "quote": "虚构引句"}]
    elif tamper == "page":
        data["communication"]["evidence"] = [{**reference(), "page": 1}]
    elif tamper == "confidence":
        data["communication"]["confidence"] = 1.01
    elif tamper == "stress":
        data["stress_tolerance"] = copy.deepcopy(data["communication"])
    elif tamper == "fake_confirmation":
        data["_portrait_assessment_confirmation"] = {"confirmed_by": "model"}
    result = PortraitGenerator().propose_behavior_assessment(
        Client(data, finish_reason="length" if tamper == "length" else "stop"),
        source_text=SOURCE, sources=SOURCES)
    assert result["status"] == "partial"
    assert result["next_action"] == "retry_or_review_assessment_sources"


def test_complete_input_not_silently_truncated():
    client = Client(proposal())
    text = SOURCE + "尾页" * 20000
    PortraitGenerator().propose_behavior_assessment(client, source_text=text,
        sources=[{**SOURCES[0], "text": text}])
    assert json.loads(client.request["messages"][1]["content"])["evidence_blocks"][0]["quote"] == text


def test_server_hydrates_exact_quote_page_block_and_hash():
    result = candidate()
    evidence = result["assessment"]["teamwork"]["evidence"][0]
    assert evidence == {**reference(), "source_id": "resume", "page": 2, "block_id": "b1", "quote": SOURCE,
                        "quote_sha256": hashlib.sha256(SOURCE.encode()).hexdigest()}
    assert "quote" not in proposal()["teamwork"]["evidence"][0]


@pytest.mark.parametrize("change", ["unknown", "other_owner", "other_page", "other_text"])
def test_references_cannot_escape_frozen_snapshot(change):
    selected = proposal()
    other = copy.deepcopy(SOURCES)
    if change == "unknown":
        foreign = {"evidence_id": "ev_unknown"}
    else:
        if change == "other_owner":
            other[0]["source_task_id"] = "another-owner-task"
        elif change == "other_page":
            other[0]["page"] = 99
        else:
            other[0]["text"] = "组织讨论"
        foreign = reference(other)
    selected["teamwork"]["evidence"] = [foreign]
    result = candidate(selected)
    assert result["reason"] == "assessment_evidence_id_not_in_snapshot"


@pytest.mark.parametrize("change", ["duplicate", "negative_page", "missing_text", "not_in_source"])
def test_bad_input_blocks_rejected_before_model_call(change):
    sources = copy.deepcopy(SOURCES)
    if change == "duplicate":
        sources.append(copy.deepcopy(sources[0]))
    elif change == "negative_page":
        sources[0]["page"] = -1
    elif change == "missing_text":
        sources[0]["text"] = ""
    else:
        sources[0]["text"] = "其他简历中的事实"
    client = Client(proposal())
    result = PortraitGenerator().propose_behavior_assessment(client, source_text=SOURCE, sources=sources)
    assert result["status"] == "partial"
    assert not hasattr(client, "request")


def test_school_page_cannot_support_behavior_even_with_valid_id():
    official = {"source_id": "https://school.example/charter", "source_kind": "official_school",
                "page": None, "block_id": "charter-3", "text": "学校是省属公办普通本科高校。"}
    sources = SOURCES + [official]
    selected = proposal()
    official_ref = reference([official])
    selected["teamwork"]["evidence"] = [official_ref]
    result = PortraitGenerator().propose_behavior_assessment(Client(selected), source_text=SOURCE, sources=sources)
    assert result["reason"] == "behavior_evidence_must_belong_to_resume"


def test_stress_context_needs_selected_resume_evidence():
    data = proposal()
    data["stress_tolerance"] = copy.deepcopy(data["communication"])
    data["stress_tolerance"]["situation"] = reference()
    assert candidate(data)["status"] == "candidate"
    # A valid ID is not itself proof of stress; still a model proposal requiring review.
    unconfirmed = profile()
    unconfirmed["portrait_assessment"] = candidate(data)
    assert PortraitGenerator()._calc_d3_quality([], unconfirmed)["stress_tolerance"]["score"] is None
    data["stress_tolerance"]["situation"] = None
    assert candidate(data)["reason"] == "stress_assessment_requires_situation_action_outcome"
