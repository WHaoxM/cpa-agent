"""Public career seed must not become source-free market evidence over TCP HTTP."""

import os
import threading

import pytest
import requests
from flask import Flask
from werkzeug.serving import make_server

from app.api import career_bp
from app.api import career as career_api


SEED_ROLE = {
    "id": 19,
    "role_name": "AI Infra工程师",
    "category": "运维",
    "sub_category": "AI基础设施",
    "avg_salary_low": 25,
    "avg_salary_high": 50,
    "demand_level": "极高",
    "hot_cities": '["北京","上海","深圳"]',
    "hot_industries": '["AI","大模型","云计算"]',
    "entry_difficulty": "高",
    "growth_trend": "上升",
    "core_skills": '["CUDA","分布式训练","GPU集群","K8s","高性能计算"]',
    "skill_count": 5,
    "description": "搭建AI训练和推理基础设施，管理GPU集群。7个岗位抢1个人。",
    "day_in_life": "早上监控GPU集群→优化训练速度→解决显存溢出→设计分布式方案",
}


class SeedReadOnlyMySQL:
    def execute(self, sql, parameters=None):
        normalized = " ".join(sql.split()).lower()
        if "count(*) as total" in normalized:
            return [{"total": 1}]
        if "group_concat(role_name" in normalized:
            return [{
                "category": "运维",
                "role_count": 1,
                "avg_salary_low": 25,
                "avg_salary_high": 50,
                "roles": "AI Infra工程师",
            }]
        return [dict(SEED_ROLE)]


def _isolated_career_app():
    """Register the production blueprint without app factory schema ensures."""
    app = Flask(__name__)
    app.register_blueprint(career_bp, url_prefix="/api/career")
    return app


def test_unverified_market_seed_is_suppressed_in_public_http(monkeypatch):
    monkeypatch.setattr(career_api, "get_mysql_client", lambda: SeedReadOnlyMySQL())
    monkeypatch.setattr(career_api, "_cache_get", lambda _key: None)
    monkeypatch.setattr(career_api, "_cache_set", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(career_api, "_milvus_semantic_match", lambda *_args: [])
    server = make_server("127.0.0.1", 0, _isolated_career_app(), threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with requests.Session() as client:
            intro = client.get(base + "/api/career/role-intro", params={"role": "AI Infra工程师"}, timeout=10)
            listing = client.get(base + "/api/career/insights", params={"limit": 1}, timeout=10)
            detail = client.get(base + "/api/career/insights/AI%20Infra工程师", timeout=10)
            landscape = client.get(base + "/api/career/landscape", timeout=10)
            explored = client.get(base + "/api/career/explore", params={"q": "CUDA"}, timeout=10)
        assert all(r.status_code == 200 for r in (intro, listing, detail, landscape, explored))
        for response in (intro, listing, detail, landscape, explored):
            body = response.json()
            assert body["status"] == "partial"
            assert body["market_data_status"] == "unverified_seed"
            assert "7个岗位抢1个人" not in response.text
            assert "25K-50K" not in response.text

        intro_data = intro.json()["data"]
        assert intro_data["title"] == "AI Infra工程师"
        assert intro_data["skills"] == ["CUDA", "分布式训练", "GPU集群", "K8s", "高性能计算"]
        assert intro_data["responsibilities"]
        assert intro_data["topRegions"] == []
        assert intro_data["outlook"] == ""

        for data in (listing.json()["data"][0], detail.json()["data"]):
            assert data["avg_salary_low"] is None
            assert data["avg_salary_high"] is None
            assert data["demand_level"] is None
            assert data["description"] is None
            assert data["core_skills"] == intro_data["skills"]
        assert landscape.json()["data"][0]["avg_salary_range"] == ""
        assert explored.json()["data"]["fuzzy_matches"][0]["description"] is None
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_market_filter_and_keyword_cannot_consume_unverified_seed(monkeypatch):
    calls = []

    class SpyMySQL(SeedReadOnlyMySQL):
        def execute(self, sql, parameters=None):
            calls.append(sql)
            return super().execute(sql, parameters)

    monkeypatch.setattr(career_api, "get_mysql_client", lambda: SpyMySQL())
    monkeypatch.setattr(career_api, "_cache_get", lambda _key: None)
    client = _isolated_career_app().test_client()
    filtered = client.get("/api/career/insights?demand_level=极高")
    assert filtered.status_code == 409
    assert filtered.get_json()["reason"] == "market_filter_unverified"
    assert calls == []

    searched = client.get("/api/career/insights?keyword=7个岗位抢1个人")
    assert searched.status_code == 200
    assert all("description LIKE" not in query for query in calls)


@pytest.mark.skipif(os.getenv("CPA_TEST_LIVE_MYSQL") != "1", reason="opt-in read-only MySQL HTTP probe")
def test_existing_live_seed_is_suppressed_over_isolated_socket(monkeypatch):
    """Use production DB reads, but never app-factory schema ensures or writes."""
    monkeypatch.setattr(career_api, "_cache_get", lambda _key: None)
    monkeypatch.setattr(career_api, "_cache_set", lambda *_args, **_kwargs: None)
    server = make_server("127.0.0.1", 0, _isolated_career_app(), threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with requests.Session() as client:
            intro = client.get(base + "/api/career/role-intro", params={"role": "AI Infra工程师"}, timeout=10)
            detail = client.get(base + "/api/career/insights/AI%20Infra工程师", timeout=10)
            listing = client.get(base + "/api/career/insights", params={"keyword": "AI Infra", "limit": 1}, timeout=10)
            landscape = client.get(base + "/api/career/landscape", timeout=10)
        assert all(r.status_code == 200 for r in (intro, detail, listing, landscape))
        assert intro.json()["data"]["title"] == "AI Infra工程师"
        assert detail.json()["data"]["role_name"] == "AI Infra工程师"
        assert listing.json()["data"][0]["role_name"] == "AI Infra工程师"
        assert all(item["avg_salary_range"] == "" for item in landscape.json()["data"])
        for response in (intro, detail, listing, landscape):
            assert response.json()["market_data_status"] == "unverified_seed"
            assert "7个岗位抢1个人" not in response.text
            assert "25K-50K" not in response.text
    finally:
        server.shutdown()
        thread.join(timeout=5)
