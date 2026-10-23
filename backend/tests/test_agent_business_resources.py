"""Cross-page confirmed resume/portrait -> owner-scoped Agent resource reads."""

import json
from types import SimpleNamespace

import pytest
from flask import Flask

from app.services.agent_runtime.business_resources import (
    PORTRAIT_URI,
    RESUME_URI,
    OVERVIEW_URI,
    list_current_resources,
    read_current_resource,
    build_ask_overview_card,
)
from app.services.agent_runtime.pre_tool_use import ToolScopeNormalizer
from app.services.tools.career_data_read import CareerDataReadTool


class BusinessRepo:
    def __init__(self):
        self.student_id = "student_001"
        self.resume_task = "task_resume1"
        self.portrait_task = "task_portrait1"
        self.candidate = {
            "name": "测试同学", "school": "测试大学", "major": "计算机", "target_role": "工程师",
            "phone": "13812345678", "email": "private@example.com",
            "skills": ["Python", "Redis", "SQL", "Vue"],
            "projects": [
                {"name": f"项目{i}", "role": "开发", "desc": "联系 private@example.com" if i == 0 else f"描述{i}"}
                for i in range(4)
            ],
            "competitions": [{"name": "竞赛甲", "level": "国家级", "award": "国家级"}],
        }
        self.profile = {**self.candidate, "_resume_confirmation": {
            "task_id": self.resume_task, "student_id": self.student_id,
            "source_evidence": {"page_count": 3, "observed_pages": [1, 2, 3]},
        }}
        self.l2 = {
            "task_id": self.portrait_task, "status": "partial",
            "source_confirmation_task_id": self.resume_task,
            "portrait_candidate": {
                "source_confirmation_task_id": self.resume_task,
                "source_self_report_revision": 0,
                "D1_basic": {"dimension_score": None, "evidence": []},
                "D2_skill": {"dimension_score": 85, "evidence": ["Python 项目", "Redis 项目", "SQL 项目", "Vue 项目"]},
                "D3_quality": {"dimension_score": None, "evidence": []},
                "D4_potential": {"dimension_score": 48, "evidence": ["项目经历"]},
                "summary": {"competitiveness_score": 99},
            },
            "missing_fields": ["communication"],
        }

    def fetch_all(self, sql, params):
        sid = params.get("sid") or params.get("student_id")
        if "FROM student_profile" in sql:
            return [{"profile_json": json.dumps(self.profile, ensure_ascii=False)}] if sid == self.student_id else []
        if "JSON_UNQUOTE" in sql:
            if sid == self.student_id and params.get("tid") == self.resume_task:
                return [{"task_id": self.portrait_task, "student_id": sid,
                         "pipeline": "L2_generate_portrait", "status": "partial",
                         "result_json": json.dumps(self.l2, ensure_ascii=False)}]
            return []
        if "FROM pipeline_task" in sql and params.get("task_id") == self.resume_task:
            return [{"task_id": self.resume_task, "student_id": self.student_id,
                     "pipeline": "L0_parse_resume", "status": "completed",
                     "result_json": json.dumps({"confirmed": True, "candidate_profile": self.candidate}, ensure_ascii=False)}]
        return []


def test_current_catalog_and_pages_are_structured_complete_and_owner_scoped():
    repo = BusinessRepo()
    catalog = list_current_resources(repo, student_id=repo.student_id)
    assert [(r["uri"], r["status"]) for r in catalog["resources"]] == [
        (OVERVIEW_URI, "completed"), (RESUME_URI, "completed"), (PORTRAIT_URI, "partial")]
    assert list_current_resources(repo, student_id="student_other")["resources"] == []

    overview = read_current_resource(repo, student_id=repo.student_id, uri=RESUME_URI)
    assert overview["data"]["source_pages"] == 3
    assert overview["data"]["counts"]["projects"] == 4
    assert "phone" not in overview["data"] and "email" not in overview["data"]
    combined = read_current_resource(repo, student_id=repo.student_id, uri=OVERVIEW_URI)
    assert [p["name"] for p in combined["data"]["first_projects"]] == ["项目0", "项目1"]
    assert combined["data"]["portrait"]["status"] == "partial"
    assert combined["data"]["portrait"]["scores"] == {"D1": None, "D2": 85, "D3": None, "D4": 48}
    assert combined["data"]["portrait"]["dimensions_with_score"] == [
        {"dimension": "D2", "score": 85}, {"dimension": "D4", "score": 48}]
    assert combined["data"]["portrait"]["dimensions_without_score"] == ["D1", "D3"]
    ask_card = build_ask_overview_card(repo, student_id=repo.student_id)
    assert "项目0" in ask_card and '"D4":48' in ask_card
    assert "private@example.com" not in ask_card
    assert "超出 Ask 本轮预算" in build_ask_overview_card(repo, student_id=repo.student_id, max_chars=50)

    first = read_current_resource(repo, student_id=repo.student_id,
                                  uri=RESUME_URI, section="projects", limit=2)
    second = read_current_resource(repo, student_id=repo.student_id,
                                   uri=RESUME_URI, section="projects", offset=2, limit=2)
    assert [x["name"] for x in first["data"] + second["data"]] == [f"项目{i}" for i in range(4)]
    assert first["coverage"]["has_more"] is True
    assert second["coverage"]["complete"] is True
    assert "private@example.com" not in json.dumps(first, ensure_ascii=False)
    with pytest.raises(LookupError):
        read_current_resource(repo, student_id="student_other", uri=RESUME_URI)


def test_partial_portrait_is_readable_but_never_published_or_total_scored():
    repo = BusinessRepo()
    result = read_current_resource(repo, student_id=repo.student_id, uri=PORTRAIT_URI)
    assert result["status"] == "partial"
    assert result["published"] is False
    assert result["data"]["total_score"] is None
    assert result["data"]["dimensions"]["D2"]["score"] == 85
    evidence = read_current_resource(repo, student_id=repo.student_id,
                                     uri=PORTRAIT_URI, section="evidence", dimension="D2", limit=3)
    assert evidence["coverage"]["has_more"] is True
    assert evidence["coverage"]["next_offset"] == 3


def test_confirmed_entity_linking_is_pageable_proposal_not_canonical_fact():
    repo = BusinessRepo()
    linking = {
        "status": "proposed", "source_id": "public_seed_v1", "source_sha256": "source_hash",
        "next_action": "confirm_resume",
        "mentions": [{"name": "Python", "evidence": "使用Python开发服务"},
                     {"name": "SQL", "evidence": "使用SQL查询"}],
        "candidates": [{"name": "Python", "source_id": "public_seed_v1"}],
        "decisions": [{"mention_index": 0, "decision": "equivalent", "candidate_index": 0,
                       "rationale": "两者指同一编程语言"},
                      {"mention_index": 1, "decision": "unresolved", "candidate_index": None,
                       "rationale": "候选库无可验证来源"}],
    }
    repo.candidate["entity_linking"] = linking
    repo.profile["entity_linking"] = linking
    catalog = list_current_resources(repo, student_id=repo.student_id)
    resume = next(item for item in catalog["resources"] if item["uri"] == RESUME_URI)
    assert "entity_links" in resume["sections"]
    first = read_current_resource(repo, student_id=repo.student_id, uri=RESUME_URI,
                                  section="entity_links", limit=1)
    second = read_current_resource(repo, student_id=repo.student_id, uri=RESUME_URI,
                                   section="entity_links", offset=1, limit=1)
    assert first["status"] == "proposed"
    assert first["data"][0]["candidate"]["name"] == "Python"
    assert first["coverage"]["has_more"] is True
    assert second["data"][0]["decision"] == "unresolved"
    assert second["data"][0]["candidate"] is None
    assert second["coverage"]["complete"] is True


def test_model_owner_argument_is_rebound_and_tool_requires_server_principal(monkeypatch):
    repo = BusinessRepo()
    args, changed, denied = ToolScopeNormalizer(
        "career_data_read", {"operation": "read", "uri": RESUME_URI, "student_id": "student_other"},
        student_id=repo.student_id, session_id=None,
    ).normalize()
    assert denied is None and changed and args["student_id"] == repo.student_id

    monkeypatch.setattr("app.services.tools.career_data_read.get_mysql_client", lambda: repo)
    app = Flask(__name__)
    with app.test_request_context("/"):
        monkeypatch.setattr("app.api._helpers.resolve_request_scope", lambda: SimpleNamespace(student_id=repo.student_id))
        output = json.loads(CareerDataReadTool().execute(operation="read", uri=RESUME_URI, section="projects", limit=2))
        assert output["success"] is True
        assert output["coverage"]["has_more"] is True
    denied_output = json.loads(CareerDataReadTool().execute(operation="list"))
    assert denied_output["status"] == "denied"


def test_evidence_operation_reuses_confirmed_resume_order_and_coverage(monkeypatch):
    from app.services.agent_runtime.tool_result_channels import build_model_view, build_ui_view
    from app.services.agent_runtime.tool_egress import sanitize_tool_call_record

    repo = BusinessRepo()
    source_order = ["PostgreSQL", "Redis", "Python", "FastAPI", "Vue", "Docker",
                    "Celery", "LangGraph", "Milvus", "Neo4j", "SQL", "Linux"]
    repo.profile["skills"] = source_order
    repo.candidate["skills"] = source_order
    monkeypatch.setattr("app.services.tools.career_data_read.get_mysql_client", lambda: repo)
    app = Flask(__name__)
    with app.test_request_context("/"):
        monkeypatch.setattr("app.api._helpers.resolve_request_scope", lambda: SimpleNamespace(student_id=repo.student_id))
        raw = CareerDataReadTool().execute(
            operation="evidence", uri=RESUME_URI, section="skills", max_items=10,
            student_id="student_other",  # model input cannot choose an owner
        )
    result = json.loads(raw)
    assert result["success"] is True and result["status"] == "partial", result
    assert [item["data"] for item in result["items"]] == source_order[:10]
    assert result["sources"][0]["version"] == repo.resume_task
    assert result["coverage"] == {
        "complete": False, "returned": 10, "total": 12, "reason": "item_limit",
    }
    assert result["next_offset"] == 10
    assert len(build_model_view("career_data_read", raw)) < 4000
    ui = build_ui_view("career_data_read", raw)
    assert ui["continuation"] == "下一页从 offset=10 继续读取"
    assert sanitize_tool_call_record(ui)["continuation"] == "下一页从 offset=10 继续读取"


def test_evidence_operation_rejects_foreign_uri_invalid_section_and_oversize(monkeypatch):
    repo = BusinessRepo()
    monkeypatch.setattr("app.services.tools.career_data_read.get_mysql_client", lambda: repo)
    app = Flask(__name__)
    with app.test_request_context("/"):
        monkeypatch.setattr("app.api._helpers.resolve_request_scope", lambda: SimpleNamespace(student_id=repo.student_id))
        tool = CareerDataReadTool()
        for args in (
            {"uri": "career://me/action-plans/plan_other", "section": "body"},
            {"uri": RESUME_URI, "section": "private_json"},
            {"uri": RESUME_URI, "section": "skills", "max_items": 11},
        ):
            result = json.loads(tool.execute(operation="evidence", **args))
            assert result["success"] is False and result["status"] == "unavailable"


def test_budget_stop_in_agent_turn_is_partial_not_fake_completion():
    from app.services.agent_runtime.blackboard import Blackboard
    from app.services.agent_runtime.mode_runtime import ModeRuntime
    from app.services.agent_runtime.turn_contract import derive_turn_outcome

    packed = ModeRuntime()._pack(
        reply="本轮预算已用尽，尚未形成完整结论。", session_id="session_test",
        start=0, active_subagent="qa_subagent", strategy="agent",
        blackboard=Blackboard(), incomplete=True,
    )
    assert derive_turn_outcome(packed)["status"] == "partial"


def test_d3_resource_reads_current_self_report_without_promoting_it_to_resume_fact():
    repo = BusinessRepo()
    repo.profile['_user_self_report'] = {'revision': 1}
    portrait = repo.l2['portrait_candidate']
    portrait['source_self_report_revision'] = 1
    portrait['D3_quality']['communication'] = {
        'evidence': '我在展示前整理大家的问题，逐项沟通并确认了分工。',
        'provenance': {'kind': 'user_self_report', 'revision': 1}, 'score': 12,
    }
    before = json.dumps(repo.profile, ensure_ascii=False, sort_keys=True)
    result = read_current_resource(repo, student_id=repo.student_id,
        uri=PORTRAIT_URI, section='evidence', dimension='D3')
    assert result['data'] == [{'trait': 'communication',
        'text': '我在展示前整理大家的问题，逐项沟通并确认了分工。',
        'provenance': {'kind': 'user_self_report', 'revision': 1}}]
    assert result['source_resume_version'] == repo.resume_task
    assert result['published'] is False and result['coverage']['total'] == 1
    assert json.dumps(repo.profile, ensure_ascii=False, sort_keys=True) == before
    with pytest.raises(LookupError):
        read_current_resource(repo, student_id='student_other', uri=PORTRAIT_URI, section='evidence', dimension='D3')


def test_ask_practice_restriction_does_not_discard_student_statements():
    from app.services.agent_runtime.mode_runtime import _ask_evidence_card
    card = _ask_evidence_card({'items': [{'data': {'provenance': {'kind': 'user_self_report'}}}]})
    assert '行动计划中的实际尝试' in card
    assert '本人补充可以按自述讨论' in card
