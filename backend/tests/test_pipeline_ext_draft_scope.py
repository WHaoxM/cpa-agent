from flask import g

from app import create_app
from app.config import Config


_SKILLS = [{"name": "Python"}]


def test_ext_draft_routes_ignore_forged_student_and_never_claim_persistence(monkeypatch):
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    app = create_app()

    @app.before_request
    def bind_test_principal():
        g.cpa_principal = {"principal_id": "alice", "student_id": "owner_A"}

    from app.pipeline.entity_extractor import l4_report_generator, portrait_generator, report_generator

    seen = []

    def full_report(student_id, *args, **kwargs):
        seen.append(student_id)
        return {"report_id": "rpt_ephemeral", "student_id": student_id}

    def match_report(student_id, *args, **kwargs):
        seen.append(student_id)
        return {"report_id": "rpt_ephemeral", "student_id": student_id}

    def career_report(student_id, *args, **kwargs):
        seen.append(student_id)
        return {"report_id": "career_ephemeral", "student_id": student_id}

    def portrait(student_id, *args, **kwargs):
        seen.append(student_id)
        return {"student_id": student_id}

    def html(self, report, llm_summary):
        assert report["student_id"] == "owner_A"
        assert report["report_id"] == "DRAFT-NOT-SAVED"
        return "<html>draft</html>"

    monkeypatch.setattr(l4_report_generator, "generate_full_report", full_report)
    monkeypatch.setattr(l4_report_generator.L4ReportGenerator, "generate_html", html)
    monkeypatch.setattr(report_generator, "generate_match_report", match_report)
    monkeypatch.setattr(report_generator, "generate_career_report", career_report)
    monkeypatch.setattr(portrait_generator, "generate_student_portrait", portrait)

    client = app.test_client()
    cases = {
        "report/full": {"student_skills": _SKILLS, "job_skills": _SKILLS},
        "report/html": {"student_skills": _SKILLS, "job_skills": _SKILLS},
        "report/match": {"student_skills": _SKILLS, "job_skills": _SKILLS},
        "report/career": {"skills": _SKILLS},
        "portrait/student": {"skills": _SKILLS},
    }
    for path, payload in cases.items():
        response = client.post(f"/api/pipeline/ext/{path}", json={"student_id": "victim_B", **payload})
        assert response.status_code == 200, path
        body = response.get_json()
        assert body["status"] == body["data"]["status"] == "partial"
        assert body["verified"] is body["data"]["verified"] is False
        assert body["data"]["persisted"] is False
        assert "report_id" not in body["data"]
        if path != "report/html":
            assert body["data"]["student_id"] == "owner_A"
    assert seen == ["owner_A"] * len(cases)


def test_ext_draft_routes_fail_closed_without_principal(monkeypatch):
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    client = create_app().test_client()
    for path in ("report/full", "report/html", "report/match", "report/career", "portrait/student"):
        response = client.post(f"/api/pipeline/ext/{path}", json={"student_id": "victim_B"})
        assert response.status_code == 401, path
