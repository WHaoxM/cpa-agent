"""Growth AIGC reads the same versioned business projections as the Agent."""

import json

from flask import g

from app import create_app
from app.api import ai_analysis as ai_api
from app.config import Config


def _client(monkeypatch):
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    app = create_app()

    @app.before_request
    def bind_principal():
        g.cpa_principal = {"principal_id": "student", "student_id": "owner_A"}

    return app.test_client()


def test_growth_analysis_rejects_partial_portrait_and_browser_snapshot(monkeypatch):
    client = _client(monkeypatch)
    reads = []

    def read_resource(_mysql, *, student_id, uri, section):
        reads.append((student_id, uri, section))
        return {"success": True, "status": "partial", "published": False,
                "uri": uri, "version": "portrait_candidate", "data": {"total_score": None}}

    monkeypatch.setattr(ai_api, "get_mysql_client", object)
    monkeypatch.setattr(
        "app.services.agent_runtime.resource_catalog.read_platform_resource", read_resource,
    )
    monkeypatch.setattr(ai_api, "_require_llm", lambda: (_ for _ in ()).throw(AssertionError("LLM must not run")))

    response = client.post("/api/ai/growth-analysis", json={
        "snapshots": [{"growthScore": 99, "title": "browser-only value"}],
    })

    body = response.get_json()
    assert response.status_code == 409
    assert body["status"] == "partial"
    assert body["data"] is None
    assert body["source_coverage"]["portrait"] == "partial"
    assert reads == [("owner_A", "career://me/portrait", "overview")]


def test_growth_analysis_uses_only_verified_report_and_portrait(monkeypatch):
    client = _client(monkeypatch)
    reads = []
    prompts = []

    def read_resource(_mysql, *, student_id, uri, section):
        reads.append((student_id, uri, section))
        if uri == "career://me/portrait":
            return {"success": True, "status": "completed", "published": True,
                    "uri": uri, "version": "portrait_v1", "source_resume_version": "resume_v1",
                    "data": {"total_score": 72, "dimensions": {"D1": {"score": 75}}}}
        return {"success": True, "status": "completed", "business_artifact_verified": True,
                "uri": uri, "version": "report_v1", "data": {"summary": "Verified summary"}}

    class FakeMySQL:
        def execute(self, sql, params=None):
            assert "FROM target_role" in sql
            assert params == {"uid": "owner_A"}
            return [{"role_name": "Backend Engineer", "priority": 1}]

    class FakeLLM:
        def chat(self, messages, **kwargs):
            prompts.append(messages[0]["content"])
            return json.dumps({
                "summary": "Based on verified report", "growthScore": 99,
                "scoreTooltip": "Estimate, not an official score", "topAbilities": [],
                "topAbilityDetails": [], "improvementAreas": [], "improvementDetails": [],
                "careerSuggestions": [], "timeline": [], "nextSteps": [],
            })

    monkeypatch.setattr(ai_api, "get_mysql_client", FakeMySQL)
    monkeypatch.setattr(
        "app.services.agent_runtime.resource_catalog.read_platform_resource", read_resource,
    )
    monkeypatch.setattr(ai_api, "load_prompt", lambda _id, *, context_json: context_json)
    monkeypatch.setattr(ai_api, "_require_llm", lambda: FakeLLM())

    response = client.post("/api/ai/growth-analysis", json={
        "reportIds": ["report_1"],
        "snapshots": [{"title": "browser-only value", "growthScore": 99}],
    })

    body = response.get_json()
    assert response.status_code == 200
    assert body["success"] is True
    assert body["data"]["growthScore"] == 72
    assert "portrait_v1" in body["data"]["scoreTooltip"]
    assert body["source_refs"]["portrait"]["version"] == "portrait_v1"
    assert body["source_refs"]["reports"] == [
        {"uri": "career://me/reports/report_1", "version": "report_v1"},
    ]
    assert "browser-only value" not in prompts[0]
    assert "Verified summary" in prompts[0]
    assert reads == [
        ("owner_A", "career://me/portrait", "overview"),
        ("owner_A", "career://me/reports/report_1", "overview"),
    ]


def test_career_suggestions_rejects_client_only_target_role(monkeypatch):
    client = _client(monkeypatch)

    class FakeMySQL:
        def execute(self, sql, params=None):
            assert "FROM target_role" in sql
            assert params == {"uid": "owner_A"}
            return []

    monkeypatch.setattr(ai_api, "get_mysql_client", FakeMySQL)
    monkeypatch.setattr(ai_api, "_require_llm", lambda: (_ for _ in ()).throw(AssertionError("LLM must not run")))
    response = client.post("/api/agent/career-suggestions", json={
        "userId": "victim_B", "targetRoles": ["Invented role"],
    })
    body = response.get_json()
    assert response.status_code == 409
    assert body["status"] == "needs_input"
    assert body["next_action"] == "save_target_role"


def test_career_suggestions_reads_owner_saved_role_and_canonical_skills(monkeypatch):
    client = _client(monkeypatch)
    captured = {}

    class FakeMySQL:
        def execute(self, sql, params=None):
            assert params == {"uid": "owner_A"}
            return [{"role_name": "Backend Engineer", "priority": 1}]

    def build_pack(_mysql, *, student_id, resource_refs, **_kwargs):
        captured["student_id"] = student_id
        captured["refs"] = resource_refs
        return {"items": [{"uri": "career://me/resume", "section": "skills",
                           "version": "resume_v1", "data": "PostgreSQL"}],
                "has_more": True, "coverage": {"complete": False, "returned": 1,
                                                "total": 35, "reason": "item_limit"},
                "sources": [{"uri": "career://me/resume", "section": "skills",
                             "version": "resume_v1"}]}

    class FakeLLM:
        def chat(self, messages, **_kwargs):
            captured["prompt"] = messages[0]["content"]
            return json.dumps({"suggestions": ["Use your confirmed PostgreSQL project evidence"]})

    monkeypatch.setattr(ai_api, "get_mysql_client", FakeMySQL)
    monkeypatch.setattr(
        "app.services.agent_runtime.evidence_retrieval.build_evidence_pack", build_pack,
    )
    monkeypatch.setattr(ai_api, "load_prompt", lambda _id, *, context_json: context_json)
    monkeypatch.setattr(ai_api, "_require_llm", lambda: FakeLLM())
    response = client.post("/api/agent/career-suggestions", json={
        "targetRoles": ["Victim-supplied frontend role"],
    })
    body = response.get_json()
    assert response.status_code == 200
    assert body["status"] == "partial"
    assert body["completion_scope"] == "draft_advice_only"
    assert body["source_coverage"]["total"] == 35
    assert captured["student_id"] == "owner_A"
    assert captured["refs"] == [{"uri": "career://me/resume", "section": "skills"}]
    assert "Backend Engineer" in captured["prompt"]
    assert "resume_v1" in captured["prompt"]
    assert "Victim-supplied frontend role" not in captured["prompt"]
