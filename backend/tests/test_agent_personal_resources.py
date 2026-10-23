"""Owner, provenance, and pagination contract for personal Agent resources."""

import pytest

from app.services.agent_runtime.personal_resources import (
    ACTION_PLANS_URI,
    LEARNING_PROGRESS_URI,
    SAVED_JOBS_URI,
    TARGET_ROLES_URI,
    accepts_personal_resource,
    list_personal_resources,
    read_personal_resource,
)


class PersonalRepo:
    def __init__(self):
        self.plan = {
            "plan_id": "plan_good1", "title": "学习计划（初稿）", "status": "draft",
            "body_text": "职业学习计划。" * 300, "body_sha256": "a" * 64,
            "artifact_id": "art_good1", "artifact_version": 1,
            "student_id": "student_001", "session_id": "session_001",
            "gap_summary": "报告指出缺少一个可复现的练习。",
            "created_at": "2026-10-10", "updated_at": "2026-10-10",
        }
        self.steps = [{
            "step_id": "step_good1", "position": 1, "title": "完成小型练习",
            "detail": "使用合成数据完成一项小练习。", "completed": 1,
            "completed_at": "2026-10-11", "evidence_summary": "我完成了一份合成练习。",
            "attachment_file_id": "file_student_001", "evidence_updated_at": "2026-10-11",
        }]
        self.saved = [
            {"id": i, "job_id": f"job_{i}", "job_title": f"岗位{i}",
             "company": "用户提交的公司", "saved_at": "2026-10-10", "salary": "面议"}
            for i in range(1, 5)
        ]
        self.progress = []
        self.roles = []
        self.queries = []

    def fetch_all(self, sql, params):
        self.queries.append((sql, params.copy()))
        if params.get("sid") != "student_001":
            return [{"n": 0, "changed": None}] if "COUNT(*)" in sql else []
        if "COUNT(*)" in sql:
            if "agent_action_plan" in sql:
                return [{"n": 1, "changed": "2026-10-10"}]
            if "FROM saved_job" in sql:
                return [{"n": len(self.saved), "changed": "2026-10-10"}]
            if "FROM learning_progress" in sql:
                return [{"n": len(self.progress), "changed": "2026-10-10"}]
            if "FROM target_role" in sql:
                return [{"n": len(self.roles), "changed": "2026-10-10"}]
            return [{"n": 0, "changed": None}]
        if "JOIN agent_artifact a" in sql and "p.plan_id=:plan_id" in sql:
            return [self.plan] if params["plan_id"] == self.plan["plan_id"] else []
        if "FROM agent_action_plan_step s" in sql:
            return self.steps[params["offset"]:params["offset"] + params["limit"]]
        if "COUNT(*) AS n FROM agent_action_plan_step" in sql:
            return [{"n": len(self.steps)}]
        if "SELECT p.plan_id,p.title" in sql:
            return [self.plan] if params["offset"] == 0 else []
        if "FROM saved_job" in sql:
            ordered = list(reversed(self.saved))
            return ordered[params["offset"]:params["offset"] + params["limit"]]
        if "FROM learning_progress lp" in sql:
            return self.progress[params["offset"]:params["offset"] + params["limit"]]
        if "FROM target_role" in sql:
            return self.roles[params["offset"]:params["offset"] + params["limit"]]
        return []


def test_personal_catalog_only_lists_present_owner_records_and_exact_uris():
    repo = PersonalRepo()
    listed = list_personal_resources(repo, student_id="student_001")
    assert [item["uri"] for item in listed] == [ACTION_PLANS_URI, SAVED_JOBS_URI]
    assert listed[0]["status"] == "draft"
    assert listed[1]["status"] == "user_submitted"
    assert list_personal_resources(repo, student_id="student_other") == []
    assert accepts_personal_resource(f"{ACTION_PLANS_URI}/plan_good1")
    assert all(accepts_personal_resource(uri) for uri in (
        ACTION_PLANS_URI, SAVED_JOBS_URI, LEARNING_PROGRESS_URI, TARGET_ROLES_URI,
    ))
    assert not accepts_personal_resource(f"{ACTION_PLANS_URI}/../plan_good1")
    assert not accepts_personal_resource("career://me/action-plans/other_user")


def test_plan_detail_is_draft_and_body_requires_explicit_chunk_continuation():
    repo = PersonalRepo()
    uri = f"{ACTION_PLANS_URI}/plan_good1"
    overview = read_personal_resource(repo, student_id="student_001", uri=uri)
    assert overview["status"] == "draft"
    assert overview["published"] is False
    assert overview["data"]["body_chunks"] > 1
    first = read_personal_resource(repo, student_id="student_001", uri=uri, section="body", limit=1)
    assert first["coverage"]["has_more"] is True
    next_page = read_personal_resource(repo, student_id="student_001", uri=uri,
                                       section="body", offset=first["coverage"]["next_offset"], limit=3)
    assert len("".join(first["data"] + next_page["data"])) == len(repo.plan["body_text"])
    assert all(params.get("sid") == "student_001" for _, params in repo.queries)
    with pytest.raises(LookupError):
        read_personal_resource(repo, student_id="student_other", uri=uri)
    with pytest.raises(LookupError):
        read_personal_resource(repo, student_id="student_001",
                               uri=f"{ACTION_PLANS_URI}/plan_unknown")


def test_plan_evidence_read_marks_student_submission_and_uses_existing_attachment_reader(monkeypatch):
    from app.services.agent_runtime.media import document_access, file_store, session_resources

    repo = PersonalRepo()
    mounted = []
    monkeypatch.setattr(file_store, "get_agent_file_service", lambda: type("Files", (), {
        "get": lambda _self, file_id: {
            "file_id": file_id, "filename": "练习记录.txt", "student_id": "student_001",
            "session_id": "session_001", "status": "ready",
        },
    })())
    monkeypatch.setattr(session_resources, "mount_resources",
                        lambda session_id, resources, *, student_id: mounted.append(
                            (session_id, resources, student_id)))
    monkeypatch.setattr(document_access, "materialize_for_ask", lambda file_ids, **kwargs: (
        "【附件：练习记录.txt】完整读取：合成数据的清洗与验证结果。",
        {"all_complete": True, "files": [{"total_chars": 24, "prompt_complete": True}]},
    ))

    result = read_personal_resource(
        repo, student_id="student_001", uri=f"{ACTION_PLANS_URI}/plan_good1",
        section="evidence", limit=1,
    )
    item = result["data"][0]
    assert item["step"]["completed"] is True
    record = item["step"]["practice_record"]
    assert record["source"] == "student_submitted" and record["verified"] is False
    assert record["attachment"]["read_status"] == "read"
    assert "完整读取" in record["attachment"]["content"]
    assert item["evidence_boundary"].startswith("练习说明和附件均由学生提交")
    assert mounted == [("session_001", [{"type": "file", "file_id": "file_student_001"}],
                        "student_001")]


def test_plan_evidence_does_not_read_cross_owner_or_not_ready_attachments(monkeypatch):
    from app.services.agent_runtime.media import file_store

    repo = PersonalRepo()
    monkeypatch.setattr(file_store, "get_agent_file_service", lambda: type("Files", (), {
        "get": lambda _self, file_id: {
            "file_id": file_id, "filename": "pending.txt", "student_id": "student_001",
            "session_id": "session_001", "status": "ingesting",
        },
    })())
    result = read_personal_resource(
        repo, student_id="student_001", uri=f"{ACTION_PLANS_URI}/plan_good1",
        section="evidence", limit=1,
    )
    attachment = result["data"][0]["step"]["practice_record"]["attachment"]
    assert attachment["read_status"] == "processing"
    assert attachment["content"] is None


def test_saved_jobs_are_paged_user_submissions_never_verified_matches():
    repo = PersonalRepo()
    first = read_personal_resource(repo, student_id="student_001", uri=SAVED_JOBS_URI,
                                   section="items", limit=3)
    second = read_personal_resource(repo, student_id="student_001", uri=SAVED_JOBS_URI,
                                    section="items", offset=3, limit=3)
    assert first["coverage"]["has_more"] is True
    assert second["coverage"]["complete"] is True
    assert [job["record_id"] for job in first["data"] + second["data"]] == [4, 3, 2, 1]
    assert all(job["source_verified"] is False and "match_score" not in job
               for job in first["data"] + second["data"])
    with pytest.raises(ValueError):
        read_personal_resource(repo, student_id="student_001", uri=SAVED_JOBS_URI,
                               section="items", limit=4)
    with pytest.raises(ValueError):
        read_personal_resource(repo, student_id="student_001", uri=SAVED_JOBS_URI,
                               section="overview", offset=1)
    with pytest.raises(PermissionError):
        list_personal_resources(repo, student_id="")


def test_plan_pii_is_redacted_before_chunk_boundaries():
    repo = PersonalRepo()
    repo.plan["body_text"] = "x" * 1197 + "private@example.com" + "y" * 40
    uri = f"{ACTION_PLANS_URI}/plan_good1"
    first = read_personal_resource(repo, student_id="student_001", uri=uri,
                                   section="body", limit=1)
    rest = read_personal_resource(repo, student_id="student_001", uri=uri,
                                  section="body", offset=1, limit=3)
    body = "".join(first["data"] + rest["data"])
    assert "private@example.com" not in body
    assert "[email]" in body


def test_progress_and_role_resources_distinguish_observations_from_sources():
    repo = PersonalRepo()
    repo.progress = [
        {"id": 7, "course_id": "course_1", "chapter_id": "3", "progress": 90,
         "completed": False, "course_title": None, "chapter_title": None,
         "source_course_id": None, "source_chapter_id": None, "updated_at": "2026-10-10"},
    ]
    repo.roles = [
        {"id": 9, "role_name": "后端工程师", "priority": 1, "updated_at": "2026-10-10"},
    ]
    uris = [item["uri"] for item in list_personal_resources(repo, student_id="student_001")]
    assert LEARNING_PROGRESS_URI in uris and TARGET_ROLES_URI in uris
    progress = read_personal_resource(repo, student_id="student_001",
                                      uri=LEARNING_PROGRESS_URI, section="items")
    assert progress["data"][0]["progress"] == 90
    assert progress["data"][0]["source_status"] == "source_missing"
    role = read_personal_resource(repo, student_id="student_001",
                                  uri=TARGET_ROLES_URI, section="items")
    assert role["data"][0]["source"] == "user_preference"
    assert all(params["sid"] == "student_001" for _, params in repo.queries)
