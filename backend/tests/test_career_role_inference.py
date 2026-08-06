"""Deep Role Inference: Neo4j REQUIRES coverage + IDF, not taxonomy-only vote."""

from __future__ import annotations

from app.api import career as career_api
from app.services.career_role_inference import (
    canonicalize_skills,
    finalize_candidates,
    idf_weight,
    infer_roles_from_skills,
    normalize_skill_inputs,
    score_insights_coverage,
    score_neo4j_coverage,
    skill_document_frequency,
)


def test_canonicalize_aliases():
    out = canonicalize_skills([("Vue3", 0.9), ("RAG", 0.8)], ["Vue.js", "RAG"])
    assert out["Vue.js"] == 0.9
    assert out["RAG"] == 0.8


def test_hub_skill_idf_downweights_python():
    rows = [
        {"role_name": "A", "skills": [{"name": "Python"}, {"name": "RAG"}]},
        {"role_name": "B", "skills": [{"name": "Python"}, {"name": "Java"}]},
        {"role_name": "C", "skills": [{"name": "Python"}]},
        {"role_name": "D", "skills": [{"name": "RAG"}, {"name": "LangChain"}]},
    ]
    df = skill_document_frequency(rows)
    assert df["Python"] == 3
    assert df["RAG"] == 2
    assert idf_weight("Python", df, 4) < idf_weight("LangChain", df, 4)


def test_neo4j_coverage_prefers_specialized_role():
    """Python+RAG should rank RAG工程师 over 后端开发 (hub Python alone)."""
    role_rows = [
        {
            "role_name": "RAG工程师",
            "category": "AI",
            "skills": [
                {"name": "Python"},
                {"name": "RAG"},
                {"name": "向量数据库"},
                {"name": "LangChain"},
            ],
        },
        {
            "role_name": "后端开发",
            "category": "后端",
            "skills": [
                {"name": "Java"},
                {"name": "MySQL"},
                {"name": "Redis"},
                {"name": "SQL"},
            ],
        },
        {
            "role_name": "AI智能体工程师",
            "category": "AI",
            "skills": [
                {"name": "Python"},
                {"name": "LangChain"},
                {"name": "RAG"},
                {"name": "Prompt Engineering"},
            ],
        },
    ]
    weights = {"Python": 0.7, "RAG": 0.95, "LangChain": 0.9, "向量数据库": 0.85}
    acc = score_neo4j_coverage(role_rows, weights)
    ranked = finalize_candidates(acc, top_k=5)
    assert ranked[0]["role_name"] in ("RAG工程师", "AI智能体工程师")
    assert ranked[0]["coverage"] > 0.4
    assert "neo4j_requires" in ranked[0]["sources"]
    # 后端开发 should not win on Python alone
    names = [r["role_name"] for r in ranked]
    if "后端开发" in names:
        assert names.index(ranked[0]["role_name"]) == 0
        assert "后端开发" != ranked[0]["role_name"]


def test_insights_coverage_adds_missing_skills():
    acc = {}
    score_insights_coverage(
        acc,
        [
            {
                "role_name": "RAG工程师",
                "core_skills": '["RAG", "Python", "向量数据库", "LangChain"]',
                "demand_level": "高",
                "entry_difficulty": "中",
            }
        ],
        {"RAG": 0.9, "Python": 0.7},
    )
    slot = acc["RAG工程师"]
    assert "向量数据库" in slot["missing_core_skills"]
    assert "career_insights" in slot["sources"]


def test_infer_end_to_end_with_fakes():
    class FakeMysql:
        def execute(self, sql, params=None):
            if "FROM skill_taxonomy" in sql and "related_roles" not in sql:
                return [{"skill_name": "RAG"}, {"skill_name": "LangChain"}, {"skill_name": "Python"}]
            if "related_roles" in sql:
                return [
                    {
                        "skill_name": "RAG",
                        "related_roles": '["随便岗"]',
                        "heat_score": 0.9,
                    }
                ]
            if "career_insights" in sql:
                return [
                    {
                        "role_name": "RAG工程师",
                        "core_skills": '["RAG", "Python", "向量数据库", "LangChain"]',
                        "category": "AI",
                        "demand_level": "高",
                        "entry_difficulty": "中",
                    }
                ]
            return []

    class FakeNeo4j:
        def execute_query(self, cypher, parameters=None, read_only=True):
            return [
                {
                    "role_name": "RAG工程师",
                    "category": "AI",
                    "skills": [
                        {"name": "Python"},
                        {"name": "RAG"},
                        {"name": "向量数据库"},
                        {"name": "LangChain"},
                    ],
                },
                {
                    "role_name": "后端开发",
                    "category": "后端",
                    "skills": [{"name": "Java"}, {"name": "MySQL"}],
                },
            ]

    out = infer_roles_from_skills(
        [
            {"name": "RAG", "confidence": 0.95},
            {"name": "LangChain", "confidence": 0.9},
            {"name": "Python", "confidence": 0.7},
        ],
        top_k=3,
        mysql_client=FakeMysql(),
        neo4j_client=FakeNeo4j(),
    )
    assert out["predicted_role"] == "RAG工程师"
    assert out["confidence"] >= 0.5
    assert out["candidates"][0]["missing_core_skills"]
    assert "evidence_summary" in out
    # Taxonomy vote must not override graph
    assert out["predicted_role"] != "随便岗"


def test_explore_rejects_resume_blob(monkeypatch):
    from app import create_app

    monkeypatch.setattr(career_api, "_cache_get", lambda key: None)
    client = create_app().test_client()
    blob = (
        "钟梓珉 实习生 AI应用开发 2006/11 13330772777 hosheil@outlook.com "
        "https://github.com/whaoxm 专业技能"
    )
    resp = client.get("/api/career/explore", query_string={"q": blob, "top_k": 5})
    assert resp.status_code == 400
    assert "infer-role" in resp.get_json()["error"]


def test_infer_role_endpoint(monkeypatch):
    from app import create_app

    monkeypatch.setattr(
        career_api,
        "infer_roles_from_skills",
        lambda skills, top_k=5, preferred_role=None, mysql_client=None, neo4j_client=None: {
            "predicted_role": "RAG工程师",
            "confidence": 0.82,
            "candidates": [
                {
                    "role_name": "RAG工程师",
                    "score": 2.1,
                    "coverage": 0.75,
                    "matched_skills": ["RAG"],
                    "missing_core_skills": ["向量数据库"],
                    "sources": ["neo4j_requires"],
                }
            ],
            "skill_count": 1,
            "evidence_summary": "ok",
        },
    )
    client = create_app().test_client()
    resp = client.post(
        "/api/career/infer-role",
        json={"skills": [{"name": "RAG", "confidence": 0.9}], "top_k": 3},
    )
    assert resp.status_code == 200
    assert resp.get_json()["data"]["predicted_role"] == "RAG工程师"


def test_normalize_skill_inputs_dedupes():
    pairs = normalize_skill_inputs(
        ["RAG", {"name": "RAG", "confidence": 0.9}, {"name": "Agent", "weight": 0.8}]
    )
    assert pairs[0] == ("RAG", 0.9)
