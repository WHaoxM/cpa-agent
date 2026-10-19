from datetime import datetime

import pytest
from flask import Flask, g

from app.api import home
from app.config import Config


class DashboardMysql:
    def __init__(self, *, courses=None, weekly=0, reports=None):
        self.courses = courses or []
        self.weekly = weekly
        self.reports = reports or []
        self.calls = []

    def execute(self, sql, params=None):
        params = params or {}
        self.calls.append((sql, params))
        if "FROM course c" in sql:
            return self.courses
        if "FROM learning_progress lp" in sql:
            return [{"total": self.weekly}]
        if "FROM career_report" in sql:
            return self.reports
        raise AssertionError(f"Unexpected dashboard SQL: {sql}")


@pytest.fixture
def dashboard_app(monkeypatch):
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    app = Flask(__name__)
    app.register_blueprint(home.home_bp, url_prefix="/api/home")

    @app.before_request
    def bind_test_principal():
        student_id = __import__("flask").request.headers.get("X-Test-Student")
        if student_id:
            g.cpa_principal = {"student_id": student_id, "principal_id": f"account:{student_id}"}

    return app.test_client()


def bind_workbench(monkeypatch, *, state="not_started", dimensions=None, confirmation_id=None):
    monkeypatch.setattr(
        "app.services.student_workbench.read_student_workbench",
        lambda mysql, student_id: {
            "source_confirmation_task_id": confirmation_id,
            "portrait": {
                "state": state,
                "dimensions": dimensions or {"D1": None, "D2": None, "D3": None, "D4": None},
                "scoring_eligible": state == "ready_scored",
            },
        },
    )


def test_dashboard_requires_server_principal_without_touching_persistence(dashboard_app, monkeypatch):
    def should_not_read_db():
        raise AssertionError("数据库不可在鉴权失败时读取")

    monkeypatch.setattr(home, "get_mysql_client", should_not_read_db)
    response = dashboard_app.get("/api/home/dashboard?student_id=student-from-query")
    assert response.status_code == 401
    assert response.get_json()["success"] is False


def test_dashboard_scopes_queries_and_returns_only_verified_ordered_records(dashboard_app, monkeypatch):
    mysql = DashboardMysql(
        courses=[
            {"course_id": "course-a", "title": "数据分析基础", "occurred_at": datetime(2026, 10, 18, 10)},
            {"course_id": "course-b", "title": "产品设计入门", "occurred_at": datetime(2026, 10, 16, 9)},
        ],
        weekly=4,
        reports=[
            {"report_id": "report-old", "title": "旧的未核验报告", "status": "completed",
             "created_at": datetime(2026, 10, 19, 7), "updated_at": datetime(2026, 10, 19, 7)},
            {"report_id": "report-current", "title": "职业规划报告", "status": "completed",
             "created_at": datetime(2026, 10, 18, 11), "updated_at": datetime(2026, 10, 18, 11)},
        ],
    )
    monkeypatch.setattr(home, "get_mysql_client", lambda: mysql)
    bind_workbench(
        monkeypatch,
        state="ready_scored",
        dimensions={"D1": 60, "D2": 75, "D3": 80, "D4": 70},
        confirmation_id="task_confirmed123",
    )
    monkeypatch.setattr(
        "app.services.agent_runtime.match_report_resources.read_match_report_resource",
        lambda mysql, *, student_id, uri: {
            "business_artifact_verified": uri.endswith("report-current"),
            "status": "completed" if uri.endswith("report-current") else "partial",
        },
    )

    response = dashboard_app.get(
        "/api/home/dashboard?student_id=attacker",
        headers={"X-Test-Student": "student-owner"},
    )

    assert response.status_code == 200
    data = response.get_json()["data"]
    assert data["metrics"]["completed_courses"] == 2
    assert data["metrics"]["weekly_completed_chapters"] == 4
    assert data["portrait"]["completeness"] == 100
    assert data["portrait"]["dimensions"] == {"D1": 60, "D2": 75, "D3": 80, "D4": 70}
    assert data["source_confirmation_task_id"] == "task_confirmed123"
    assert [item["title"] for item in data["recent_activities"]] == [
        "职业规划报告", "数据分析基础", "产品设计入门",
    ]
    assert all(item["route"].startswith("/app/student/") for item in data["recent_activities"])
    assert all(call[1].get("student_id") == "student-owner" for call in mysql.calls)
    weekly_params = next(params for sql, params in mysql.calls if "FROM learning_progress lp" in sql)
    assert weekly_params["week_start"].weekday() == 0
    assert (weekly_params["week_end"] - weekly_params["week_start"]).days == 7


def test_dashboard_empty_and_partial_portrait_have_no_invented_values(dashboard_app, monkeypatch):
    mysql = DashboardMysql()
    monkeypatch.setattr(home, "get_mysql_client", lambda: mysql)
    bind_workbench(
        monkeypatch,
        state="ready_with_unknowns",
        dimensions={"D1": 61, "D2": None, "D3": 73, "D4": None},
        confirmation_id="task_confirmed456",
    )
    monkeypatch.setattr(
        "app.services.agent_runtime.match_report_resources.read_match_report_resource",
        lambda *args, **kwargs: {"business_artifact_verified": False, "status": "partial"},
    )

    response = dashboard_app.get("/api/home/dashboard", headers={"X-Test-Student": "student-empty"})

    assert response.status_code == 200
    data = response.get_json()["data"]
    assert data["metrics"]["completed_courses"] == 0
    assert data["metrics"]["weekly_completed_chapters"] == 0
    assert data["recent_activities"] == []
    assert data["portrait"]["completeness"] == 50
    assert data["portrait"]["dimensions"] == {"D1": 61, "D2": None, "D3": 73, "D4": None}

    bind_workbench(monkeypatch, state="not_started")
    empty_response = dashboard_app.get("/api/home/dashboard", headers={"X-Test-Student": "student-empty"})
    empty_portrait = empty_response.get_json()["data"]["portrait"]
    assert empty_portrait["completeness"] is None
    assert empty_portrait["dimensions"] == {"D1": None, "D2": None, "D3": None, "D4": None}


def test_invalid_current_profile_does_not_replace_other_dashboard_evidence(dashboard_app, monkeypatch):
    mysql = DashboardMysql(weekly=2)
    monkeypatch.setattr(home, "get_mysql_client", lambda: mysql)

    def invalid_workbench(mysql, *, student_id):
        from app.services.student_workbench import WorkbenchSourceInvalid
        raise WorkbenchSourceInvalid("当前简历确认来源未通过校验")

    monkeypatch.setattr("app.services.student_workbench.read_student_workbench", invalid_workbench)
    monkeypatch.setattr(
        "app.services.agent_runtime.match_report_resources.read_match_report_resource",
        lambda *args, **kwargs: {"business_artifact_verified": False},
    )

    response = dashboard_app.get("/api/home/dashboard", headers={"X-Test-Student": "student-invalid-profile"})

    assert response.status_code == 200
    data = response.get_json()["data"]
    assert data["metrics"]["weekly_completed_chapters"] == 2
    assert data["portrait"]["state"] == "source_invalid"
    assert data["portrait"]["completeness"] is None
    assert all(value is None for value in data["portrait"]["dimensions"].values())
    assert data["portrait"]["error"] == "当前简历确认来源未通过校验"
