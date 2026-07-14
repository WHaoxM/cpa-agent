"""Golden Demo Path endpoint behavior without external services."""

from app import create_app
from app.api import admin_kb
from app.api import career as career_api
from app.api import pipeline as pipeline_api
from app.api import report as report_api


class _FakePipelineMySQL:
    def __init__(self):
        self.tasks = {}

    def execute_write(self, sql, parameters=None):
        parameters = parameters or {}
        normalized = " ".join(sql.split()).lower()
        if normalized.startswith("insert into pipeline_task"):
            self.tasks[parameters["task_id"]] = {
                "task_id": parameters["task_id"],
                "pipeline": parameters["pipeline"],
                "status": parameters["status"],
                "progress": 0,
                "result_json": None,
                "error_message": None,
                "created_at": parameters["created_at"],
            }
        elif normalized.startswith("update pipeline_task"):
            task = self.tasks[parameters["task_id"]]
            task["status"] = "completed"
            task["progress"] = 100
            task["result_json"] = parameters["result"]
            task["error_message"] = None
        return []

    def execute(self, sql, parameters=None):
        parameters = parameters or {}
        task = self.tasks.get(parameters.get("task_id"))
        return [task] if task else []


class _FakeReportMySQL:
    def __init__(self):
        self.reports = {}

    def execute_write(self, sql, parameters=None):
        parameters = parameters or {}
        normalized = " ".join(sql.split()).lower()
        if normalized.startswith("insert into career_report"):
            self.reports[parameters["report_id"]] = {
                "id": len(self.reports) + 1,
                "report_id": parameters["report_id"],
                "student_id": parameters["student_id"],
                "job_portrait_id": parameters["job_portrait_id"],
                "title": parameters["title"],
                "status": parameters["status"],
                "ch1_json": parameters["ch1_json"],
                "ch2_json": parameters["ch2_json"],
                "ch3_json": parameters["ch3_json"],
                "ch4_json": parameters["ch4_json"],
                "ch5_json": parameters["ch5_json"],
                "ch6_json": parameters["ch6_json"],
                "report_json": parameters["report_json"],
                "html_path": None,
                "pdf_path": None,
                "elapsed_seconds": parameters["elapsed_seconds"],
                "created_at": "2026-06-18T00:00:00",
                "updated_at": "2026-06-18T00:00:00",
                "student_name": None,
                "school": None,
                "position_name": None,
            }
        return []

    def execute(self, sql, parameters=None):
        parameters = parameters or {}
        normalized = " ".join(sql.split()).lower()
        if "where cr.report_id = :report_id" in normalized:
            report = self.reports.get(parameters.get("report_id"))
            return [report] if report else []
        if normalized.startswith("select count(*) as total"):
            return [{"total": len(self.reports)}]
        if "from career_report cr" in normalized:
            return list(self.reports.values())
        return []


def test_pipeline_list_returns_demo_pipeline_catalog():
    app = create_app()
    client = app.test_client()

    response = client.get("/api/pipeline/list")

    body = response.get_json()
    assert response.status_code == 200
    assert body["success"] is True
    assert [item["name"] for item in body["data"]] == [
        "L0_parse_resume",
        "L1_build_graph",
        "L2_generate_portrait",
        "L3_run_match",
        "L4_generate_report",
    ]


def test_pipeline_trigger_completes_demo_task_without_worker(monkeypatch):
    app = create_app()
    client = app.test_client()
    fake_mysql = _FakePipelineMySQL()

    monkeypatch.setattr(pipeline_api, "get_mysql_client", lambda: fake_mysql)
    monkeypatch.setattr(pipeline_api.Config, "DEMO_MODE", True)

    response = client.post("/api/pipeline/trigger", json={
        "pipeline": "L0_parse_resume",
        "student_id": "student_demo_001",
        "resume_text": "熟悉 Python、MySQL、Redis 和 Docker，了解 RAG。",
        "target_role": "AI应用工程师",
    })

    body = response.get_json()
    assert response.status_code == 200
    assert body["success"] is True
    assert body["data"]["status"] == "completed"
    assert body["data"]["progress"] == 100
    assert body["data"]["result"]["source"] == "demo_fallback"
    assert "Python" in body["data"]["result"]["summary"]["detected_skills"]

    status_response = client.get(f"/api/pipeline/status/{body['data']['task_id']}")
    status_body = status_response.get_json()
    assert status_response.status_code == 200
    assert status_body["data"]["status"] == "completed"
    assert status_body["data"]["progress"] == 100


def test_report_generate_persists_completed_demo_report(monkeypatch):
    app = create_app()
    client = app.test_client()
    fake_mysql = _FakeReportMySQL()

    monkeypatch.setattr(report_api, "get_mysql_client", lambda: fake_mysql)
    monkeypatch.setattr(report_api.Config, "DEMO_MODE", True)

    response = client.post("/api/report/generate", json={
        "student_id": "student_demo_001",
        "job_portrait_id": "portrait_frontend_001",
        "title": "Demo career report",
        "type": "career",
        "snapshot": {
            "targetRoles": ["Frontend engineer"],
            "topMatchScore": 88,
        },
    })

    body = response.get_json()
    assert response.status_code == 200
    assert body["success"] is True
    assert body["data"]["status"] == "completed"
    assert body["data"]["source"] == "demo_fallback"

    report_id = body["data"]["report_id"]
    detail_response = client.get(f"/api/report/{report_id}")
    detail_body = detail_response.get_json()
    assert detail_response.status_code == 200
    assert detail_body["success"] is True
    assert detail_body["data"]["status"] == "completed"
    assert detail_body["data"]["report_json"]["snapshot"]["topMatchScore"] == 88


def test_agent_chat_demo_mode_does_not_require_llm_or_session_store():
    app = create_app()
    client = app.test_client()

    response = client.post("/api/agent/chat", json={
        "message": "我想了解 AI 智能体工程师路线",
        "mode": "demo",
    })

    body = response.get_json()
    assert response.status_code == 200
    assert body["success"] is True
    assert body["data"]["engine"] == "demo"
    assert body["data"]["source"] == "demo_fallback"
    assert body["data"]["session_id"].startswith("demo_")


def test_career_landscape_returns_demo_fallback_when_mysql_is_unavailable(monkeypatch):
    app = create_app()
    client = app.test_client()

    def raise_mysql_error():
        raise RuntimeError("mysql unavailable")

    monkeypatch.setattr(career_api, "_cache_get", lambda key: None)
    monkeypatch.setattr(career_api, "get_mysql_client", raise_mysql_error)

    response = client.get("/api/career/landscape")

    body = response.get_json()
    assert response.status_code == 200
    assert body["success"] is True
    assert body["source"] == "demo_fallback"
    assert body["count"] >= 1


def test_admin_kb_smart_upload_extracts_entities_without_mysql(monkeypatch):
    app = create_app()
    client = app.test_client()

    def raise_mysql_error():
        raise RuntimeError("mysql unavailable")

    monkeypatch.setattr(admin_kb, "get_mysql_client", raise_mysql_error)
    monkeypatch.setattr(
        admin_kb,
        "_extract_entities",
        lambda content, source_type: admin_kb._fallback_extract(content),
    )

    response = client.post("/api/admin/knowledge-base/smart-upload", json={
        "type": "jd",
        "title": "AI智能体工程师岗位",
        "content": "AI智能体工程师需要 Python、RAG、Docker 和 LangChain 能力。",
    })

    body = response.get_json()
    assert response.status_code == 200
    assert body["success"] is True
    assert body["data"]["source"] == "demo_fallback"
    assert "Python" in body["data"]["parsed_entities"]["skills"]
    assert "AI智能体工程师" in body["data"]["parsed_entities"]["roles"]
    assert body["data"]["update_result"]["source"] == "demo_fallback"
