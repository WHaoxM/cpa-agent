from datetime import datetime

import pytest
from flask import Flask, g

from app.api import home
from app.config import Config


class DashboardMysql:
    def __init__(self, *, courses=None, weekly=0, reports=None, learning=None, learning_error=False):
        self.courses = courses or []
        self.weekly = weekly
        self.reports = reports or []
        self.calls = []
        self.learning = learning or []
        self.learning_error = learning_error

    def execute(self, sql, params=None):
        params = params or {}
        self.calls.append((sql, params))
        if "AS completed_chapters" in sql:
            if self.learning_error:
                raise ConnectionError('private database details')
            return self.learning
        if "FROM course c" in sql:
            return self.courses
        if "FROM learning_progress lp" in sql:
            return [{"total": self.weekly}]
        if "FROM career_report" in sql:
            return self.reports
        raise AssertionError(f"Unexpected dashboard SQL: {sql}")

    def fetch_one(self, sql, params=None):
        self.calls.append((sql, params or {}))
        return None


@pytest.fixture
def dashboard_app(monkeypatch):
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    monkeypatch.setattr("app.services.resume_draft.read_draft", lambda mysql, student_id: {
        "revision": 0, "text": "", "updated_at": None, "guidance": None,
    })
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


def test_home_continues_latest_candidate_not_previous_readable_results(dashboard_app, monkeypatch):
    mysql = DashboardMysql()
    monkeypatch.setattr(home, "get_mysql_client", lambda: mysql)
    monkeypatch.setattr("app.services.student_workbench.read_student_workbench", lambda db, student_id: {
        "run": {"state": "waiting_for_user", "task_id": "task_new"},
        "source_confirmation_task_id": "task_previous", "based_on_previous_upload": True,
        "portrait": {"state": "ready_with_unknowns", "dimensions": {"D2": 70}},
        "resume_diagnostic": {"state": "ready", "report_id": "resume_previous"},
        "analysis_run": {"state": "settled", "has_failures": False},
    })
    monkeypatch.setattr("app.services.resume_draft.read_draft", lambda db, student_id: {
        "revision": 3, "text": "本人草稿，不应进入首页正文", "updated_at": "2026-10-22T10:00:00",
        "guidance": {"status": "stale"},
    })
    data = dashboard_app.get("/api/home/dashboard?student_id=other", headers={"X-Test-Student": "owner"}).get_json()["data"]
    resume = data["resume_continuation"]
    assert resume["state"] == "waiting_for_user"
    assert resume["task_id"] == "task_new"
    assert resume["confirmation_task_id"] == "task_previous"
    assert resume["draft"] == {"state": "saved", "revision": 3, "updated_at": "2026-10-22T10:00:00"}
    assert "本人草稿" not in str(resume)
    assert "unsubmitted" not in str(resume)


@pytest.mark.parametrize("run,analysis,portrait,diagnostic,expected", [
    ("queued", "settled", "ready_with_unknowns", "ready", "queued"),
    ("running", "settled", "ready_with_unknowns", "ready", "parsing"),
    ("failed", "settled", "ready_with_unknowns", "ready", "upload_failed"),
    ("settled", "running", "processing", "partial", "analysis_running"),
    ("settled", "settled", "ready_with_unknowns", "partial", "results_ready"),
    ("settled", "settled", "failed", "failed", "analysis_failed"),
    ("settled", "not_started", "not_started", "not_started", "confirmed"),
])
def test_home_resume_states_keep_partial_readable_and_separate_processing(run, analysis, portrait, diagnostic, expected):
    result = home._resume_projection({
        "run": {"state": run, "task_id": "task_current"},
        "source_confirmation_task_id": "task_current",
        "portrait": {"state": portrait},
        "resume_diagnostic": {"state": diagnostic, "report_id": "resume_current"},
        "analysis_run": {"state": analysis, "has_failures": portrait == "failed"},
    }, {"state": "empty", "revision": 0, "updated_at": None})
    assert result["state"] == expected
    assert result["diagnostic_ready"] == (diagnostic in {"ready", "partial"})


def test_draft_only_is_not_inferred_from_stale_guidance_or_save_time():
    draft = {"state": "saved", "revision": 2, "updated_at": "2026-10-22T12:00:00"}
    assert home._resume_projection({}, draft)["state"] == "draft_only"
    assert home._resume_projection({}, {"state": "unavailable"})["state"] == "unavailable"


def test_resume_read_failure_does_not_hide_learning_or_claim_empty(dashboard_app, monkeypatch):
    mysql = DashboardMysql(weekly=2)
    monkeypatch.setattr(home, "get_mysql_client", lambda: mysql)
    def unavailable(*args, **kwargs):
        raise ConnectionError("internal connection details")
    monkeypatch.setattr("app.services.student_workbench.read_student_workbench", unavailable)
    monkeypatch.setattr("app.services.resume_draft.read_draft", unavailable)
    response = dashboard_app.get("/api/home/dashboard", headers={"X-Test-Student": "owner"})
    assert response.status_code == 200
    data = response.get_json()["data"]
    assert data["metrics"]["weekly_completed_chapters"] == 2
    assert data["resume_continuation"]["state"] == "unavailable"
    assert data["resume_continuation"]["draft"]["state"] == "unavailable"
    assert data["recent_activities_state"] == "partial"
    assert "internal connection" not in str(data)


def test_resume_timeline_uses_current_owner_source_and_real_dates_only():
    class TimelineMysql:
        calls = []
        def fetch_one(self, sql, params):
            self.calls.append((sql, params))
            if "FROM student_profile" in sql:
                return {"confirmed_at": "2026-10-22T12:00:00"}
            return {"report_id": "resume_current", "created_at": datetime(2026, 10, 22, 12, 1)}
    mysql = TimelineMysql()
    bench = {"source_confirmation_task_id": "task_current",
             "resume_diagnostic": {"state": "partial", "report_id": "resume_current"}}
    events = home._resume_activities(mysql, "owner", bench)
    assert [e["type"] for e in events] == ["resume_confirmed", "resume_diagnostic"]
    assert all(e["task_id"] == "task_current" for e in events)
    assert events[1]["partial"] is True
    assert all(params["student_id"] == "owner" and params["task_id"] == "task_current" for _, params in mysql.calls)
    assert home._resume_activities(mysql, "owner", {"read_state": "source_invalid"}) == []


def test_home_learning_summary_is_owner_scoped_and_keeps_counts(dashboard_app, monkeypatch):
    mysql = DashboardMysql(learning=[{'course_id':'python', 'title':'Python 入门', 'chapter_count':3,
                                     'completed_chapters':1, 'updated_at':datetime(2026,10,22,12)}])
    monkeypatch.setattr(home, 'get_mysql_client', lambda: mysql)
    bind_workbench(monkeypatch)
    response = dashboard_app.get('/api/home/dashboard?student_id=other', headers={'X-Test-Student':'owner'})
    assert response.status_code == 200
    summary = response.get_json()['data']['learning_continuation']
    assert summary == {'state':'ready', 'courses':[{'course_id':'python','title':'Python 入门',
                        'chapter_count':3,'completed_chapters':1,'updated_at':'2026-10-22T12:00:00'}]}
    sql, params = next((sql, params) for sql, params in mysql.calls if 'AS completed_chapters' in sql)
    assert params == {'student_id':'owner'}
    assert 'lp.progress = 100' in sql and 'lp.completed = 1' in sql
    assert 'lp.chapter_id = CAST(ch.id AS CHAR)' in sql
    assert 'HAVING' in sql and 'LIMIT 3' in sql and 'c.course_id ASC' in sql


@pytest.mark.parametrize('failed,state', [(False,'empty'),(True,'unavailable')])
def test_home_learning_empty_is_not_failure_and_does_not_hide_resume(dashboard_app, monkeypatch, failed, state):
    mysql = DashboardMysql(weekly=2, learning_error=failed)
    monkeypatch.setattr(home, 'get_mysql_client', lambda: mysql)
    bind_workbench(monkeypatch)
    response = dashboard_app.get('/api/home/dashboard', headers={'X-Test-Student':'owner'})
    assert response.status_code == 200
    data = response.get_json()['data']
    assert data['learning_continuation'] == {'state':state,'courses':[]}
    assert data['resume_continuation']['state'] == 'empty'
    assert data['metrics']['weekly_completed_chapters'] == 2
    assert 'private database' not in str(data)
