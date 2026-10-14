"""A saved target-role card must not publish unreviewed market seed or raw jobs."""

import os
import threading
from importlib import import_module

import pytest
import requests
from flask import Flask
from werkzeug.serving import make_server

from app.api import favorites_bp

repo_module = import_module("app.repositories.favorites_repo")


SEED_INSIGHT = {
    "role_name": "AI Infra工程师",
    "avg_salary_low": 25,
    "avg_salary_high": 50,
    "demand_level": "极高",
    "hot_cities": '["北京","上海","深圳"]',
    "hot_industries": '["AI","大模型","云计算"]',
    "core_skills": '["CUDA","分布式训练","GPU集群"]',
    "description": "搭建基础设施。7个岗位抢1个人。",
}


def _isolated_app():
    """The production blueprint, without app-factory schema ensures or writes."""
    app = Flask(__name__)
    app.register_blueprint(favorites_bp, url_prefix="/api")
    return app


def _socket_app():
    server = make_server("127.0.0.1", 0, _isolated_app(), threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread, f"http://127.0.0.1:{server.server_port}"


def test_target_role_market_does_not_promote_seed_or_unpublished_jobs(monkeypatch):
    queries = []

    class SeedWithUnpublishedJobs:
        def execute(self, sql, _parameters=None):
            queries.append(sql)
            if "job_posting" in sql:
                raise AssertionError("unpublished job_posting must not be read for a market card")
            assert "FROM career_insights" in sql
            return [dict(SEED_INSIGHT)]

    monkeypatch.setattr(repo_module, "get_mysql_client", lambda: SeedWithUnpublishedJobs())
    server, thread, base = _socket_app()
    try:
        response = requests.get(base + "/api/target-role-market/AI%20Infra工程师", timeout=10)
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["status"] == "partial"
        assert body["market_data_status"] == "unverified_seed"
        assert body["source_coverage"]["market_claims"] == "unverified_seed"
        assert set(body["unavailable_fields"]) >= {
            "salaryRange", "medianSalary", "demandLevel", "demandCount",
            "hotCities", "industries", "trendNote", "referenceMatch",
        }
        data = body["data"]
        assert data["role"] == "AI Infra工程师"
        assert data["skillTags"] == ["CUDA", "分布式训练", "GPU集群"]
        assert data["salaryRange"] == "—"
        assert data["medianSalary"] is None
        assert data["demandLevel"] is None
        assert data["demandCount"] is None
        assert data["hotCities"] == []
        assert data["industries"] == []
        assert data["sampleJobs"] == []
        assert data["trendNote"] == ""
        assert data["referenceMatch"] is None
        assert data["marketDataStatus"] == "unverified_seed"
        assert "25K-50K" not in response.text
        assert "7个岗位抢1个人" not in response.text
        assert len(queries) == 1
        assert "avg_salary" not in queries[0]
        assert "demand_level" not in queries[0]
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_target_role_market_missing_catalogue_is_no_result(monkeypatch):
    class NoCatalogue:
        def execute(self, sql, _parameters=None):
            assert "job_posting" not in sql
            return []

    monkeypatch.setattr(repo_module, "get_mysql_client", lambda: NoCatalogue())
    response = _isolated_app().test_client().get("/api/target-role-market/不存在职位名称")
    assert response.status_code == 200
    body = response.get_json()
    assert body["success"] is True
    assert body["status"] == "no_result"
    assert body["data"] is None


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_MYSQL") != "1", reason="opt-in read-only MySQL HTTP probe")
def test_existing_live_seed_cannot_leak_over_isolated_socket():
    """Real MySQL SELECT via production route; no schema ensure, transaction or writes."""
    from app.services.db import get_mysql_client

    mysql = get_mysql_client()
    assert mysql.execute(
        "SELECT role_name FROM career_insights WHERE role_name=:role",
        {"role": "AI Infra工程师"},
    )
    server, thread, base = _socket_app()
    try:
        response = requests.get(base + "/api/target-role-market/AI%20Infra工程师", timeout=10)
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "partial"
        assert body["market_data_status"] == "unverified_seed"
        assert body["data"]["role"] == "AI Infra工程师"
        assert body["data"]["skillTags"]
        assert "25K-50K" not in response.text
        assert "7个岗位抢1个人" not in response.text
    finally:
        server.shutdown()
        thread.join(timeout=5)
