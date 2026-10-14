import os
import uuid

import pytest
from flask import g

from app import create_app
from app.api import learning as learning_api
from app.config import Config
from app.services.db.mysql_client import get_mysql_client


def test_learning_report_converts_percent_progress_to_study_hours(monkeypatch):
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    app = create_app()

    @app.before_request
    def bind_principal():
        g.cpa_principal = {"principal_id": "alice", "student_id": "owner_A"}

    class FakeMySQL:
        def execute(self, sql, params):
            assert params == {"user_id": "owner_A"}
            # With a 2-hour chapter at 25%, the report must yield 0.5 hours,
            # not the unscaled 50 percent-hours produced by the old expression.
            assert "progress / 100.0" in sql
            return [{"total_study_time": 0.5, "completed_courses": 0, "average_progress": 25.0}]

    monkeypatch.setattr(learning_api, "get_mysql_client", FakeMySQL)
    response = app.test_client().get("/api/learning/learning/report/owner_A")
    assert response.status_code == 200
    assert response.get_json()["data"]["total_study_time"] == 0.5


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_MYSQL") != "1", reason="requires explicit live MySQL opt-in")
def test_learning_report_counts_only_explicitly_completed_courses(monkeypatch):
    """HTTP report must not label a course completed just because progress exists."""
    monkeypatch.setattr(Config, "DEMO_MODE", False)
    db = get_mysql_client()
    tag = "learning_report_" + uuid.uuid4().hex[:16]
    course_id = tag
    user_id = tag
    chapter_id = None
    app = create_app()

    @app.before_request
    def bind_principal():
        g.cpa_principal = {"principal_id": user_id, "student_id": user_id}

    try:
        db.execute(
            "INSERT INTO course (course_id,title) VALUES (:course,:title)",
            {"course": course_id, "title": tag}, commit=True,
        )
        db.execute(
            "INSERT INTO chapter (course_id,title,duration_hours) VALUES (:course,:title,2)",
            {"course": course_id, "title": tag}, commit=True,
        )
        chapter = db.execute_single(
            "SELECT id FROM chapter WHERE course_id=:course AND title=:title",
            {"course": course_id, "title": tag},
        )
        chapter_id = str(chapter["id"])
        db.execute(
            "INSERT INTO learning_progress (user_id,course_id,chapter_id,progress,completed) "
            "VALUES (:user,:course,:chapter,50,0)",
            {"user": user_id, "course": course_id, "chapter": chapter_id}, commit=True,
        )

        response = app.test_client().get(f"/api/learning/learning/report/{user_id}")
        assert response.status_code == 200
        payload = response.get_json()["data"]
        # Existing implementation returns 1: COUNT(DISTINCT course_id) counts any activity.
        assert payload["completed_courses"] == 0
        assert payload["total_study_time"] == pytest.approx(1.0)
    finally:
        db.execute("DELETE FROM learning_progress WHERE user_id=:user", {"user": user_id}, commit=True)
        if chapter_id:
            db.execute("DELETE FROM chapter WHERE id=:chapter", {"chapter": chapter_id}, commit=True)
        db.execute("DELETE FROM course WHERE course_id=:course", {"course": course_id}, commit=True)
