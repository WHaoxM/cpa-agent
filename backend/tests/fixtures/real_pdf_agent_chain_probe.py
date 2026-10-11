"""Real PDF/API/model/database probe with an isolated owner, never student_001.

Intentionally retain generated rows/files for inspection: printed owner/task IDs
identify the exact audit scope. No synthetic resume or model responses are used.
"""
import argparse
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True)
    args = parser.parse_args()
    from flask import Flask, g
    from app.api._helpers import Principal
    from app.api.profile import profile_bp
    from app.api.report import report_bp
    from app.api.pipeline import pipeline_bp
    from app.config import Config
    from app.services.tools.career_data_read import CareerDataReadTool
    assert not Config.DEMO_MODE, "strict Principal mode required"
    owner = "golden_" + uuid.uuid4().hex[:12]
    app = Flask(__name__)
    app.register_blueprint(profile_bp, url_prefix="/api/profile")
    app.register_blueprint(report_bp, url_prefix="/api/report")
    app.register_blueprint(pipeline_bp, url_prefix="/api/pipeline")

    @app.before_request
    def principal():
        g.cpa_principal = Principal("probe:" + owner, owner, "test")

    def show(stage, **values):
        print(json.dumps({"stage": stage, "owner": owner, **values}, ensure_ascii=False, default=str), flush=True)

    show("start", retained_for_audit=True, transport="real Flask API test client; real model/DB/PDF")
    client = app.test_client()
    with open(args.pdf, "rb") as stream:
        response = client.post("/api/profile/student/upload", data={"file": (stream, "resume.pdf")},
                               content_type="multipart/form-data")
    body = response.json or {}
    data = body.get("data") or {}
    profile = data.get("candidate_profile") or {}
    linking = profile.get("entity_linking") or {}
    show("upload", http=response.status_code, status=data.get("status"), task_id=data.get("task_id"),
         pages=(data.get("source_evidence") or {}).get("page_count"), blocks=data.get("block_count"),
         counts={k: len(profile.get(k) or []) for k in ("projects", "competitions", "skills", "research")},
         structure_calls=profile.get("structure_calls"), entity_call=profile.get("entity_extraction_call"),
         linking_status=linking.get("status"), link_call=linking.get("call"), retrieval=linking.get("retrieval"),
         error=body.get("error"))
    if response.status_code != 200 or data.get("status") != "needs_input":
        return
    response = client.post("/api/profile/student/confirm", json={"task_id": data["task_id"], "confirmed": True})
    show("confirm", http=response.status_code, data=response.json)
    if response.status_code != 200:
        return
    response = client.post("/api/report/resume-diagnostic", json={})
    report = (response.json or {}).get("data") or {}
    show("report", http=response.status_code, report_id=report.get("report_id"), status=report.get("status"),
         model_usage=report.get("model_usage"), error=(response.json or {}).get("error"))
    response = client.get("/api/pipeline/entity-review")
    review = (response.json or {}).get("data") or {}
    if response.status_code == 200 and review.get("mentions"):
        response = client.post("/api/pipeline/entity-review", json={
            "source_confirmation_task_id": review["source_confirmation_task_id"],
            "source_sha256": review["source_sha256"], "request_id": "probe_" + uuid.uuid4().hex,
            "expected_revision": review["revision"],
            "reviews": [{"mention_index": 0, "action": "defer", "note": "隔离回放测试，暂缓不发布"}]})
        show("review", http=response.status_code, revision=((response.json or {}).get("data") or {}).get("revision"))
    else:
        show("review", http=response.status_code, reason="no_reviewable_mentions")
    with app.test_request_context("/"):
        g.cpa_principal = Principal("probe:" + owner, owner, "test")
        read = json.loads(CareerDataReadTool().execute(operation="read", uri="career://me/resume",
                                                      section="entity_links", limit=1))
    show("agent_read", success=read.get("success"), review=read.get("review"),
         review_state=(read.get("data") or [{}])[0].get("review_state"), coverage=read.get("coverage"))


if __name__ == "__main__":
    main()
