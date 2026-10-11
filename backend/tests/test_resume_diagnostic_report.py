import json
from types import SimpleNamespace

from app.services.resume_diagnostic_report import (
    build_resume_diagnostic_report, _synthesize, _synthesize_role_directions,
    read_resume_diagnostic)
from app.services.job_publication import canonical_json, content_sha256


def _profile():
    return {
        "name": "张同学", "school": "测试大学", "major": "计算机科学",
        "projects": [{"name": "检索项目", "role": "开发", "desc": "实现检索项目"}],
        "research": [{"name": "论文甲", "level": "校级"}],
        "competitions": [{"name": "竞赛甲", "award": "二等奖"}],
        "leadership_roles": ["项目组长"], "skills": ["Python"],
        "source": "canonical_llm", "raw_text_chars": 100,
        "structure_calls": [{"usage": {"total_tokens": 100}}],
        "entity_extraction_call": {"usage": {"total_tokens": 30}},
        "entity_extraction_status": "partial", "entity_extraction_issues": [{"index": 1}],
        "entity_linking": {"status": "proposed", "mentions": [
            {"name": "Python", "evidence": "Python"}], "candidates": [
            {"name": "Python 语言"}], "decisions": [
            {"mention_index": 0, "decision": "related", "candidate_index": 0,
             "rationale": "同一语言线索"}]},
        "_resume_confirmation": {"task_id": "l0_test", "student_id": "student_test",
            "source_evidence": {"file_id": "file_test", "page_count": 2,
                "observed_pages": [1, 2], "missing_pages": [], "accuracy_verified": False,
                "blocks": [
                    {"block_id": "b1", "page": 1, "text": "测试大学 计算机科学 Python 项目组长"},
                    {"block_id": "b2", "page": 2, "text": "检索项目 实现检索项目 论文甲 竞赛甲"}]}}
    }


def test_resume_report_preserves_all_facts_without_job_claims():
    report = build_resume_diagnostic_report(_profile(), student_id="student_test")
    assert report["status"] == "partial"  # factual draft is not semantic completion
    assert report["report_type"] == "resume_diagnostic"
    chapters = {x["id"]: x for x in report["chapters"]}
    assert len(chapters["experience"]["items"]) == 2
    assert len(chapters["achievements"]["items"]) == 2
    assert chapters["entity_review"]["items"][0]["fields"]["review_status"] == "proposed_not_confirmed"
    assert chapters["role_directions"]["status"] == "pending_model_interpretation"
    assert chapters["experience"]["items"][0]["evidence_refs"][0]["page"] == 2
    text = json.dumps(report, ensure_ascii=False)
    assert "raw_text_chars" not in text and "canonical_llm" not in text
    assert "entity_extraction_call" not in text and "structure_calls" not in text
    assert "match_score" not in text and "salary" not in text


def test_semantic_synthesis_requires_existing_evidence_ids():
    report = build_resume_diagnostic_report(_profile(), student_id="student_test")
    class Client:
        def chat_with_metadata(self, **kwargs):
            return SimpleNamespace(text=json.dumps({
                "summary": {"text": "有计算机项目与竞赛经历。", "evidence_ids": ["projects_0", "competitions_0"]},
                "strengths": [{"text": "有项目实践线索。", "evidence_ids": ["projects_0"]}],
                "questions": []}, ensure_ascii=False), finish_reason="stop",
                context_status=SimpleNamespace(fits=True), usage={"total_tokens": 300}, model="test-model")
    report = _synthesize(report, llm_client=Client())
    assert report["status"] == "partial"  # role directions still missing
    assert report["analysis_status"] == "model_interpretation_with_references"

    class Hallucinating(Client):
        def chat_with_metadata(self, **kwargs):
            result = super().chat_with_metadata(**kwargs)
            data = json.loads(result.text)
            data["summary"]["evidence_ids"] = ["nonexistent"]
            result.text = json.dumps(data)
            return result
    invalid = _synthesize(build_resume_diagnostic_report(_profile(), student_id="student_test"),
                          llm_client=Hallucinating())
    assert invalid["status"] == "partial"
    assert invalid["analysis"] is None


def test_role_directions_are_evidence_grounded_but_not_catalog_jobs():
    class Client:
        def chat_with_metadata(self, **kwargs):
            assert "张同学" not in kwargs["messages"][1]["content"]
            return SimpleNamespace(text=json.dumps({"directions": [{
                "title": "检索应用开发方向",
                "evidence_ids": ["projects_0"],
                "questions_to_verify": ["实际承担的职责是什么？"]}]}, ensure_ascii=False),
                finish_reason="stop", context_status=SimpleNamespace(fits=True),
                usage={"total_tokens": 123}, model="test-model")
    report = _synthesize_role_directions(build_resume_diagnostic_report(_profile(),
                                                                          student_id="student_test"),
                                         llm_client=Client())
    chapter = next(ch for ch in report["chapters"] if ch["id"] == "role_directions")
    assert chapter["status"] == "model_interpretation_unverified"
    assert chapter["items"][0]["fields"]["source_type"] == "resume_model_inference"
    assert chapter["items"][0]["evidence_refs"][0]["page"] == 2
    assert "resume_based_role_directions" not in report["missing_sections"]
    assert "job_portrait_id" not in json.dumps(chapter)

    class Invalid(Client):
        def chat_with_metadata(self, **kwargs):
            result = super().chat_with_metadata(**kwargs)
            payload = json.loads(result.text)
            payload["directions"][0]["evidence_ids"] = ["absent"]
            result.text = json.dumps(payload)
            return result
    bad = _synthesize_role_directions(build_resume_diagnostic_report(_profile(),
                                                                       student_id="student_test"),
                                      llm_client=Invalid())
    assert next(ch for ch in bad["chapters"] if ch["id"] == "role_directions")["items"] == []
    assert "resume_based_role_directions" in bad["missing_sections"]


def test_role_direction_citations_follow_model_fact_order():
    class Client:
        def chat_with_metadata(self, **kwargs):
            return SimpleNamespace(text=json.dumps({"directions": [{
                "title": "检索方向", "evidence_ids": ["skills_0", "projects_0"],
                "questions_to_verify": ["希望专注哪类岗位？"]}]}),
                finish_reason="stop", context_status=SimpleNamespace(fits=True),
                usage={"total_tokens": 40}, model="test-model")

    report = _synthesize_role_directions(
        build_resume_diagnostic_report(_profile(), student_id="student_test"),
        llm_client=Client(),
    )
    role = next(ch for ch in report["chapters"] if ch["id"] == "role_directions")["items"][0]
    assert [ref["page"] for ref in role["evidence_refs"][:2]] == [1, 2]
    assert role["fields"]["evidence_ids"] == ["skills_0", "projects_0"]


def test_old_report_get_preserves_facts_and_explicitly_requests_upgrade(monkeypatch):
    import app.services.resume_diagnostic_report as service
    profile = _profile()
    legacy = build_resume_diagnostic_report(profile, student_id="student_test")
    legacy["chapters"] = [ch for ch in legacy["chapters"] if ch["id"] != "role_directions"]
    legacy["status"] = "completed"
    legacy["missing_sections"] = []
    row = {"report_id": legacy["report_id"], "student_id": "student_test",
           "source_confirmation_task_id": "l0_test", "status": "completed",
           "report_json": canonical_json(legacy), "content_sha256": content_sha256(legacy)}
    class DB:
        def fetch_all(self, sql, params):
            return [row]
    monkeypatch.setattr(service, "require_confirmed_profile", lambda _db, _sid: profile)
    catalog_version = ["empty-v1"]
    monkeypatch.setattr("app.services.job_catalog_listing.list_published_job_candidates",
                        lambda *args, **kwargs: {"status": "empty", "total": 0,
                                                "items": [], "catalog_version": catalog_version[0]})
    result = read_resume_diagnostic(DB(), student_id="student_test")
    assert result["status"] == "partial"
    assert next(ch for ch in result["chapters"] if ch["id"] == "role_directions")["status"] == "pending_upgrade"
    assert next(ch for ch in result["chapters"] if ch["id"] == "experience")["items"]
    assert result["job_catalog"]["status"] == "empty"
    assert "resume_based_role_directions" in result["missing_sections"]
    catalog_version[0] = "empty-v2"
    refreshed = read_resume_diagnostic(DB(), student_id="student_test")
    assert refreshed["job_catalog"]["catalog_version"] == "empty-v2"
    assert refreshed["chapters"] == result["chapters"]
