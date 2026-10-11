"""Golden-path boundary tests: no real model, graph or database calls."""
import json
from types import SimpleNamespace
from io import BytesIO

import pytest
from flask import Flask, g

from app.services import pipeline
from app.services.tools.pipeline_run import PipelineRunTool


class MemoryDB:
    def __init__(self):
        self.writes = []
        self.profile = None
        self.tasks = {}
        self.portrait = None

    def execute(self, sql, params=None, commit=False):
        params = params or {}
        self.writes.append((sql, params))
        if "INSERT INTO pipeline_task" in sql:
            self.tasks[params["task_id"]] = dict(params)
        elif "UPDATE pipeline_task" in sql:
            self.tasks.setdefault(params["task_id"], {}).update(params)
        elif "INSERT INTO student_profile" in sql:
            self.profile = json.loads(params["pj"])
        elif "INSERT INTO student_portrait_4d" in sql:
            self.portrait = {"id": 42, "portrait_json": params["pj"]}
        return []

    def fetch_all(self, sql, params=None):
        if "JSON_UNQUOTE(JSON_EXTRACT(result_json" in sql:
            matched = []
            for row in reversed(list(self.tasks.values())):
                payload = row.get("result_json") or {}
                payload = json.loads(payload) if isinstance(payload, str) else payload
                if (row.get("student_id") == params["student_id"]
                        and row.get("pipeline") == "L2_generate_portrait"
                        and payload.get("source_confirmation_task_id") == params["task_id"]):
                    matched.append(row)
            return matched[:1]
        if "pipeline_task" in sql:
            row = self.tasks.get(params.get("task_id") or params.get("tid"))
            if row and params.get("sid") and row.get("student_id") != params["sid"]:
                return []
            return [row] if row else []
        if "student_portrait_4d" in sql:
            return [self.portrait] if self.portrait else []
        return [{"profile_json": self.profile}] if self.profile else []


@pytest.fixture
def parsed(monkeypatch):
    from app.services.agent_runtime import resume_confirmation
    monkeypatch.setattr(resume_confirmation, "run_confirmation", lambda **kw: {
        "workflow": "resume_confirmation", "task_id": kw["task_id"], "resume_state": "available"})
    from app.pipeline.entity_extractor.l0_full_parser import L0FullParser
    from app.pipeline.entity_extractor.nsle_pipeline import NSLEPipeline
    result = SimpleNamespace(success=True, text="第一页 Python\n第二页 学校",
        metadata={"pages": 2}, engine="fixture", blocks=[
            SimpleNamespace(block_id="b1", text="第一页 Python", page=0, block_type="text"),
            SimpleNamespace(block_id="b2", text="第二页 学校", page=1, block_type="text")])
    monkeypatch.setattr(L0FullParser, "parse_text", lambda self, *a, **k: result)
    monkeypatch.setattr(L0FullParser, "parse_file", lambda self, *a, **k: result)
    monkeypatch.setattr(NSLEPipeline, "__init__", lambda self, *a, **k: None)
    monkeypatch.setattr(NSLEPipeline, "run_resume_text", lambda self, text: {
        "success": True, "student_profile": {"school": "学校", "major": "计算机", "skills": ["Python"]},
        "structure_validation": {"ok": True}, "skills": ["Python"]})
    return result


def test_l0_stores_candidate_not_confirmed_profile(monkeypatch, parsed):
    db = MemoryDB()
    monkeypatch.setattr(pipeline, "get_mysql_client", lambda: db)
    result = pipeline.PipelineRunner().run("L0_parse_resume", {"student_id": "owner", "text": parsed.text})
    assert result["status"] == "needs_input"
    assert result["profile_ready"] is False
    assert db.profile is None
    stored = json.loads(db.tasks[result["task_id"]]["result_json"])
    assert stored["candidate_profile"]["school"] == "学校"
    assert [b["page"] for b in stored["source_evidence"]["blocks"]] == [1, 2]


def test_agent_preserves_needs_confirmation_and_authoritative_task(monkeypatch, parsed):
    db = MemoryDB()
    tool = PipelineRunTool()
    tool._mysql = db
    result = json.loads(tool.execute(pipeline="L0_parse_resume", params={"text": parsed.text}, student_id="owner"))
    assert result["status"] == "needs_input"
    assert result["task_id"] in db.tasks
    assert result["confirmation_workflow"]["task_id"] == result["task_id"]
    assert db.tasks[result["task_id"]]["status"] != "completed"
    assert all(c["status"] != "completed" for c in result["artifact_candidates"])


@pytest.mark.parametrize("cls", [pipeline.L2GeneratePortraitPipeline, pipeline.L3RunMatchPipeline,
                                 pipeline.L4GenerateReportPipeline])
def test_downstream_rejects_unconfirmed_profile_before_external_work(cls):
    db = MemoryDB()
    db.profile = {"school": "legacy", "skills": ["Python"]}
    with pytest.raises(ValueError, match="确认"):
        cls("task_test", {"student_id": "owner", "skills": [{"name": "Python"}]}, db).run()
    assert not any("INSERT INTO student_portrait" in sql or "INSERT INTO career_report" in sql for sql, _ in db.writes)


def _candidate(db, monkeypatch, parsed):
    monkeypatch.setattr(pipeline, "get_mysql_client", lambda: db)
    return pipeline.PipelineRunner().run("L0_parse_resume", {"student_id": "owner", "text": parsed.text})


def test_confirm_is_owner_scoped_and_readback_verified(monkeypatch, parsed):
    db = MemoryDB()
    candidate = _candidate(db, monkeypatch, parsed)
    with pytest.raises(LookupError):
        pipeline.confirm_resume_candidate(db, student_id="intruder", task_id=candidate["task_id"])
    assert db.profile is None
    result = pipeline.confirm_resume_candidate(db, student_id="owner", task_id=candidate["task_id"])
    assert result["resource_id"] == "owner"
    assert result["portrait_ready"] is False
    assert db.tasks[candidate["task_id"]]["status"] == "completed"
    assert pipeline.require_confirmed_profile(db, "owner")["school"] == "学校"
    assert pipeline.confirm_resume_candidate(db, student_id="owner", task_id=candidate["task_id"])["profile_ready"]
    db.profile["school"] = "forged"
    with pytest.raises(ValueError, match="确认来源"):
        pipeline.require_confirmed_profile(db, "owner")


def test_partial_pages_require_explicit_acknowledgement(monkeypatch, parsed):
    db = MemoryDB()
    parsed.blocks.pop()
    candidate = _candidate(db, monkeypatch, parsed)
    assert candidate["source_evidence"]["missing_pages"] == [2]
    with pytest.raises(ValueError, match="未提取页面"):
        pipeline.confirm_resume_candidate(db, student_id="owner", task_id=candidate["task_id"])
    assert db.profile is None
    pipeline.confirm_resume_candidate(db, student_id="owner", task_id=candidate["task_id"], acknowledge_partial=True)
    assert db.profile["_resume_confirmation"]["partial_acknowledged"] is True


def test_confirmed_l2_uses_server_profile_and_returns_real_record(monkeypatch, parsed):
    db = MemoryDB()
    candidate = _candidate(db, monkeypatch, parsed)
    pipeline.confirm_resume_candidate(db, student_id="owner", task_id=candidate["task_id"])
    from app.pipeline.entity_extractor.portrait_generator import PortraitGenerator
    seen = {}
    def portrait(self, **kwargs):
        seen.update(kwargs)
        return {
            "status": "completed",
            "D1_basic": {"dimension_score": 60},
            "D2_skill": {"dimension_score": 65, "skill_coverage": 0.65},
            "D3_quality": {"dimension_score": 62},
            "D4_potential": {"dimension_score": 65},
            "summary": {"competitiveness_score": 63, "competitiveness_level": "中等"},
        }
    monkeypatch.setattr(PortraitGenerator, "generate_student_portrait", portrait)
    result = pipeline.PipelineRunner().run("L2_generate_portrait", {
        "student_id": "owner", "skills": [{"name": "forged"}], "student_profile": {"school": "forged"}})
    assert seen["skills"] == [{"name": "Python"}]
    assert seen["student_profile"]["school"] == "学校"
    assert result["portrait_id"] == "42"
    assert db.portrait["id"] == int(result["resource_id"])


def test_upload_confirm_and_page_l2_share_agent_runner(monkeypatch, parsed, tmp_path):
    from app.api import profile as api
    from app.config import Config
    db = MemoryDB()
    from app.services.agent_runtime import resume_confirmation
    monkeypatch.setattr(resume_confirmation, "run_confirmation", lambda **kw:
        pipeline.confirm_resume_candidate(db, student_id=kw["student_id"], task_id=kw["task_id"],
            acknowledge_partial=kw.get("acknowledge_partial", False)))
    monkeypatch.setattr(pipeline, "get_mysql_client", lambda: db)
    monkeypatch.setattr(api, "get_mysql_client", lambda: db)
    monkeypatch.setattr(Config, "UPLOAD_FOLDER", str(tmp_path))
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    app = Flask(__name__)
    app.register_blueprint(api.profile_bp, url_prefix="/api/profile")
    identity = {"principal_id": "owner", "student_id": "owner"}
    @app.before_request
    def principal():
        g.cpa_principal = identity
    client = app.test_client()
    response = client.post("/api/profile/student/upload", data={"file": (BytesIO(b"fixture"), "resume.pdf")})
    assert response.status_code == 200
    data = response.json["data"]
    assert data["status"] == "needs_input" and db.profile is None
    assert "file_path" not in data
    loaded = client.get(f"/api/profile/student/candidate/{data['task_id']}")
    assert loaded.status_code == 200
    assert loaded.json["data"]["candidate_profile"] == data["candidate_profile"]
    assert loaded.json["data"]["source_evidence"]["task_id"] == data["task_id"]
    assert client.get("/api/profile/student/candidate/task_missing").status_code == 404
    db.tasks[data["task_id"]]["student_id"] = "someone_else"
    assert client.get(f"/api/profile/student/candidate/{data['task_id']}").status_code == 404
    db.tasks[data["task_id"]]["student_id"] = "owner"
    response = client.post("/api/profile/student/confirm", json={"task_id": data["task_id"], "confirmed": True, "student_id": "victim"})
    assert response.status_code == 200
    assert response.json["data"]["resource_id"] == "owner"
    resumed = client.get(f"/api/profile/student/candidate/{data['task_id']}")
    assert resumed.status_code == 200
    assert resumed.json["data"]["confirmed"] is True
    assert resumed.json["data"]["profile_ready"] is True
    assert resumed.json["data"]["portrait_task"] is None
    from app.pipeline.entity_extractor.portrait_generator import PortraitGenerator
    monkeypatch.setattr(PortraitGenerator, "generate_student_portrait", lambda *a, **k: {
        "status": "completed",
        "D1_basic": {"dimension_score": 60}, "D2_skill": {"dimension_score": 65},
        "D3_quality": {"dimension_score": 62}, "D4_potential": {"dimension_score": 65},
        "summary": {"competitiveness_score": 63, "competitiveness_level": "中等"},
    })
    response = client.post("/api/profile/student/pipeline", json={"pipeline": "L2_generate_portrait", "student_id": "victim"})
    assert response.status_code == 200
    assert response.json["data"]["portrait_id"] == "42"
    assert response.json["data"]["student_id"] == "owner"
    from app.services.agent_runtime.pipeline_reference import resolve_pipeline_task_ref
    assert resolve_pipeline_task_ref(db, student_id="owner", task_id=response.json["data"]["task_id"])
    resumed = client.get(f"/api/profile/student/candidate/{data['task_id']}")
    assert resumed.status_code == 200, resumed.json
    assert resumed.json["data"]["portrait_task"]["task_id"] == response.json["data"]["task_id"]
    assert resumed.json["data"]["portrait_task"]["status"] == "completed"
    for stage in ("L3_run_match", "L4_generate_report"):
        response = client.post("/api/profile/student/pipeline", json={"pipeline": stage})
        assert response.status_code == 200
        assert response.json["success"] is True
        assert response.json["status"] == "partial"
        assert response.json["data"]["resource_type"] == "pipeline_task"
        assert not response.json["data"].get("report_id")
        assert not response.json["data"].get("match_id")


def test_l2_missing_semantic_evidence_keeps_task_partial_without_portrait_row(monkeypatch, parsed):
    db = MemoryDB()
    candidate = _candidate(db, monkeypatch, parsed)
    pipeline.confirm_resume_candidate(db, student_id="owner", task_id=candidate["task_id"])
    from app.pipeline.entity_extractor.portrait_generator import PortraitGenerator
    monkeypatch.setattr(PortraitGenerator, "generate_student_portrait", lambda *a, **k: {
        "status": "partial", "D1_basic": {"dimension_score": None},
        "D2_skill": {"dimension_score": 70}, "D3_quality": {"dimension_score": None},
        "D4_potential": {"dimension_score": 40},
        "summary": {"competitiveness_score": None},
        "missing_fields": ["school_category", "quality_evidence"],
    })

    result = pipeline.PipelineRunner().run("L2_generate_portrait", {"student_id": "owner"})

    assert result["status"] == "partial"
    assert result["portrait_id"] is None
    assert result["next_action"] == "confirm_semantic_facts"
    assert result["action_available"] is False
    assert result["resume_state"] == "not_available"
    assert db.portrait is None
    assert db.tasks[result["task_id"]]["status"] == "partial"


def test_confirmed_candidate_replay_links_only_current_partial_portrait(monkeypatch, parsed):
    from app.api import profile as api
    from app.config import Config
    from app.pipeline.entity_extractor.portrait_generator import PortraitGenerator

    db = MemoryDB()
    monkeypatch.setattr(pipeline, "get_mysql_client", lambda: db)
    monkeypatch.setattr(api, "get_mysql_client", lambda: db)
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    app = Flask(__name__)
    app.register_blueprint(api.profile_bp, url_prefix="/api/profile")
    identity = {"principal_id": "owner", "student_id": "owner"}
    @app.before_request
    def principal():
        g.cpa_principal = identity
    client = app.test_client()

    candidate = pipeline.PipelineRunner().run("L0_parse_resume", {"student_id": "owner", "text": parsed.text})
    pipeline.confirm_resume_candidate(db, student_id="owner", task_id=candidate["task_id"])
    monkeypatch.setattr(PortraitGenerator, "generate_student_portrait", lambda *a, **k: {
        "status": "partial", "D1_basic": {"dimension_score": None},
        "D2_skill": {"dimension_score": 70}, "D3_quality": {"dimension_score": None},
        "D4_potential": {"dimension_score": 40},
        "summary": {"competitiveness_score": None},
        "missing_fields": ["school_tier", "communication"],
    })
    partial = pipeline.PipelineRunner().run("L2_generate_portrait", {"student_id": "owner"})
    replay = client.get(f"/api/profile/student/candidate/{candidate['task_id']}")
    assert replay.status_code == 200, replay.json
    state = replay.json["data"]
    assert state["confirmed"] is True
    assert state["portrait_task"]["task_id"] == partial["task_id"]
    assert state["portrait_task"]["status"] == "partial"
    assert state["portrait_task"]["portrait_id"] is None
    assert state["portrait_task"]["portrait_candidate"]["D2_skill"]["dimension_score"] == 70
    assert state["portrait_task"]["portrait_candidate"]["summary"]["competitiveness_score"] is None
    assert state["portrait_task"]["action_available"] is False

    replacement = pipeline.PipelineRunner().run("L0_parse_resume", {"student_id": "owner", "text": parsed.text})
    pipeline.confirm_resume_candidate(db, student_id="owner", task_id=replacement["task_id"])
    assert client.get(f"/api/profile/student/candidate/{candidate['task_id']}").status_code == 409
    current = client.get(f"/api/profile/student/candidate/{replacement['task_id']}")
    assert current.status_code == 200
    assert current.json["data"]["portrait_task"] is None
    identity.update({"principal_id": "intruder", "student_id": "intruder"})
    assert client.get(f"/api/profile/student/candidate/{replacement['task_id']}").status_code == 404


def test_failed_structuring_never_overwrites_confirmed_profile(monkeypatch, parsed):
    db = MemoryDB()
    db.profile = {"school": "existing"}
    from app.pipeline.entity_extractor.nsle_pipeline import NSLEPipeline
    monkeypatch.setattr(NSLEPipeline, "run_resume_text", lambda *a, **k: {"success": False})
    monkeypatch.setattr(pipeline, "get_mysql_client", lambda: db)
    with pytest.raises(RuntimeError, match="结构化失败"):
        pipeline.PipelineRunner().run("L0_parse_resume", {"student_id": "owner", "text": parsed.text})
    assert db.profile == {"school": "existing"}
    assert all(t["status"] == "failed" for t in db.tasks.values())


def test_invalid_structure_is_partial_and_cannot_be_confirmed(monkeypatch, parsed):
    db = MemoryDB()
    from app.pipeline.entity_extractor.nsle_pipeline import NSLEPipeline
    monkeypatch.setattr(NSLEPipeline, "run_resume_text", lambda *a, **k: {
        "success": True, "student_profile": {"skills": ["Python"]}, "structure_validation": {"ok": False}})
    candidate = _candidate(db, monkeypatch, parsed)
    assert candidate["status"] == "partial"
    with pytest.raises(ValueError):
        pipeline.confirm_resume_candidate(db, student_id="owner", task_id=candidate["task_id"])
    assert db.profile is None


def test_l4_without_authoritative_l2_does_not_generate_draft(monkeypatch, parsed):
    db = MemoryDB()
    candidate = _candidate(db, monkeypatch, parsed)
    pipeline.confirm_resume_candidate(db, student_id="owner", task_id=candidate["task_id"])
    from app.pipeline.entity_extractor.report_generator import ReportGenerator
    def forbidden(*args, **kwargs):
        raise AssertionError("Missing authoritative inputs must not generate a report")
    monkeypatch.setattr(ReportGenerator, "generate_career_report", forbidden)
    result = pipeline.PipelineRunner().run("L4_generate_report", {"student_id": "owner", "job_id": "catalog_job"})
    assert result["status"] == "partial"
    assert db.tasks[result["task_id"]]["status"] == "partial"
    assert result["reason"] == "confirmed_portrait_unavailable"
    assert not result.get("report_id")
    assert not any("career_report" in sql for sql, _ in db.writes)


def test_l3_ignores_model_facts_and_stays_partial(monkeypatch, parsed):
    class CatalogDB(MemoryDB):
        def fetch_all(self, sql, params=None):
            if "FROM job_portrait_4d" in sql:
                return [{"portrait_json": {"required_skills": ["Python"]}}]
            return super().fetch_all(sql, params)

        def query_one(self, sql, params=None):
            return None
    db = CatalogDB()
    candidate = _candidate(db, monkeypatch, parsed)
    pipeline.confirm_resume_candidate(db, student_id="owner", task_id=candidate["task_id"])
    from app.pipeline.entity_extractor import matcher, graph_storage
    monkeypatch.setattr(graph_storage, "GraphStorage", lambda: SimpleNamespace())
    seen = {}
    def match(student_skills, job_skills, student_profile):
        seen.update(student=student_skills, job=job_skills)
        return {"total_score": 60, "d1_score": 50, "d2_score": 50, "d3_score": 50,
                "d4_score": 50, "skill_coverage": 1, "gap_priorities": []}
    monkeypatch.setattr(matcher, "calculate_match_score", match)
    result = pipeline.PipelineRunner().run("L3_run_match", {"student_id": "owner", "job_id": "catalog_job",
        "student_skills": [{"name": "forged"}], "job_skills": [{"name": "forged"}]})
    assert seen == {}
    assert result["reason"] == "confirmed_portrait_unavailable"
    assert not result.get("match_id")
    assert result["status"] == db.tasks[result["task_id"]]["status"] == "partial"
