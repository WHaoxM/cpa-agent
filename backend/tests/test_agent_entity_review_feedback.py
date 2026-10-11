"""Review page -> actual MySQL journal -> Principal-scoped Agent resource."""
import json

import pytest
from flask import Flask, g, request, jsonify

from app.api._helpers import Principal
from app.api.pipeline import pipeline_bp
from app.config import Config
from app.services.agent_runtime.business_resources import RESUME_URI, read_current_resource
from app.services.tools.career_data_read import CareerDataReadTool
from backend.tests.test_agent_business_resources import BusinessRepo
from backend.tests.test_resume_entity_review import review_db, _payload


def test_real_mysql_page_review_is_immediately_visible_to_agent(review_db, monkeypatch):
    mysql, owner, other, tid, linking = review_db
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    monkeypatch.setattr("app.services.tools.career_data_read.get_mysql_client", lambda: mysql)
    app = Flask(__name__)
    app.register_blueprint(pipeline_bp, url_prefix="/api/pipeline")

    @app.before_request
    def auth():
        sid = request.headers.get("X-Test-Owner")
        if sid:
            g.cpa_principal = Principal("test:" + sid, sid, "test")

    @app.get("/agent-read")
    def read():
        return jsonify(json.loads(CareerDataReadTool().execute(operation="read", uri=RESUME_URI,
            section="entity_links", offset=int(request.args.get("offset", 0)), limit=1,
            student_id=request.args.get("student_id"))))

    client = app.test_client()
    headers = {"X-Test-Owner": owner}
    first = client.get("/agent-read", headers=headers).json
    assert first["review"]["revision"] == 0
    assert first["data"][0]["review_state"] == "proposed"
    assert first["coverage"]["next_offset"] == 1
    accepted = _payload(tid, linking["source_sha256"], [{"mention_index": 1, "action": "accept"}])
    response = client.post("/api/pipeline/entity-review", headers=headers, json=accepted)
    assert response.status_code == 200
    second = client.get("/agent-read?offset=1", headers=headers).json
    assert second["review"]["revision"] == 1
    assert second["data"][0]["mention_index"] == 1
    assert second["data"][0]["review_state"] == "accepted"
    assert second["data"][0]["accepted_relation"] == "related"
    assert second["data"][0]["decision"] == "related"
    assert isinstance(second["review"]["graph_publication_status"], str)
    assert second["coverage"]["complete"] is True
    rejected = _payload(tid, linking["source_sha256"], [
        {"mention_index": 1, "action": "reject", "note": "不是同一能力"},
        {"mention_index": 0, "action": "defer"}], revision=1)
    assert client.post("/api/pipeline/entity-review", headers=headers, json=rejected).status_code == 200
    reread = client.get("/agent-read?offset=1", headers=headers).json
    assert reread["review"]["revision"] == 2
    assert reread["data"][0]["review_state"] == "rejected"
    assert reread["data"][0]["accepted_relation"] is None
    assert reread["data"][0]["review"]["note"] == "不是同一能力"
    from app.services.agent_runtime.tool_result_channels import build_model_view
    model_view = build_model_view("career_data_read", json.dumps(reread, ensure_ascii=False))
    assert '"review_revision": 2' in model_view
    assert '"review_state": "rejected"' in model_view
    assert client.get("/agent-read", headers=headers).json["data"][0]["review_state"] == "deferred"
    denied = client.get("/agent-read?student_id=" + owner, headers={"X-Test-Owner": other}).json
    assert denied["success"] is False and "data" not in denied
    unauthenticated = client.get("/agent-read?student_id=" + owner).json
    assert unauthenticated["success"] is False and "data" not in unauthenticated


def test_historical_resume_has_explicit_unavailable_review():
    repo = BusinessRepo()
    result = read_current_resource(repo, student_id=repo.student_id, uri=RESUME_URI, section="entity_links")
    assert result["status"] == "not_available"
    assert result["review"]["revision"] is None
    assert result["data"] == []


def test_source_changed_during_review_read_is_not_mixed(monkeypatch):
    repo = BusinessRepo()
    linking = {"mentions": [], "candidates": [], "decisions": [], "source_sha256": "a" * 64}
    repo.candidate["entity_linking"] = linking
    repo.profile["entity_linking"] = linking
    monkeypatch.setattr("app.services.resume_entity_review.read_entity_review", lambda *a, **kw: {
        "source_confirmation_task_id": "task_new", "source_sha256": "b" * 64})
    with pytest.raises(ValueError, match="版本"):
        read_current_resource(repo, student_id=repo.student_id, uri=RESUME_URI, section="entity_links")


def test_review_db_failure_is_not_misreported_as_unreviewed(monkeypatch):
    repo = BusinessRepo()
    linking = {"mentions": [{"name": "Python", "evidence": "Python"}],
               "candidates": [], "decisions": [], "source_sha256": "a" * 64}
    repo.candidate["entity_linking"] = linking
    repo.profile["entity_linking"] = linking
    def unavailable(*args, **kwargs):
        raise RuntimeError("journal offline")
    monkeypatch.setattr("app.services.resume_entity_review.read_entity_review", unavailable)
    result = read_current_resource(repo, student_id=repo.student_id, uri=RESUME_URI, section="entity_links")
    assert result["review"]["status"] == "unavailable"
    assert result["review"]["revision"] is None
    assert result["data"][0]["review_state"] == "unavailable"


def test_evidence_pack_never_mixes_review_revisions(monkeypatch):
    from app.services.agent_runtime import evidence_retrieval as evidence
    monkeypatch.setattr(evidence, "list_platform_resources", lambda *a, **kw: {
        "resources": [{"uri": RESUME_URI, "sections": ["entity_links"]}]})
    def read(*args, **kwargs):
        offset = kwargs["offset"]
        return {"success": True, "status": "proposed", "version": "task_same",
                "state_revision": 1 if offset == 0 else 2,
                "data": [{"review_state": "accepted", "review_revision": 1}],
                "coverage": {"total": 2, "has_more": offset == 0, "next_offset": offset + 1}}
    monkeypatch.setattr(evidence, "read_platform_resource", read)
    result = evidence.build_evidence_pack(None, student_id="owned", question="",
        resource_refs=[{"uri": RESUME_URI, "section": "entity_links"}], max_items=10)
    assert result["status"] == "partial"
    assert len(result["items"]) == 1
    assert result["items"][0]["state_revision"] == 1
    assert result["unavailable"][0]["status"] == "state_revision_changed"
    first = evidence.build_evidence_pack(None, student_id="owned", question="",
        resource_refs=[{"uri": RESUME_URI, "section": "entity_links"}], max_items=1)
    cursor = first["continuations"][0]
    assert cursor["state_revision"] == 1
    second = evidence.build_evidence_pack(None, student_id="owned", question="", continuation=cursor)
    assert second["status"] == "unavailable"
    assert not second["items"]
    assert second["unavailable"][0]["status"] == "state_revision_changed"
