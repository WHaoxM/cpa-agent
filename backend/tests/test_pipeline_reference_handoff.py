import json
from unittest.mock import patch

import pytest

from app.services.agent_runtime.pipeline_reference import resolve_pipeline_task_ref
from app.services.agent_runtime.media.prepare_turn import prepare_user_turn


class FakeMysql:
    def __init__(self, tasks=None, portraits=None):
        self.tasks = tasks or {}
        self.portraits = portraits or {}

    def fetch_all(self, query, params):
        if "FROM school_tier_review" in query:
            return []
        if "FROM pipeline_task" in query:
            row = self.tasks.get(params["tid"])
            return [row] if row and row["student_id"] == params["sid"] else []
        if "FROM student_portrait_4d" in query:
            row = self.portraits.get(params["pid"])
            return [row] if row and row["student_id"] == params["sid"] else []
        raise AssertionError(query)


def task(owner="alice", status="needs_input", result=None, pipeline="L0_parse_resume"):
    return {"task_id": "task_abc", "student_id": owner, "pipeline": pipeline,
            "status": status, "result_json": json.dumps(result or {"task_id": "task_abc", "student_id": owner})}


def test_reference_owner_and_unverified_status():
    mysql = FakeMysql({"task_abc": task()})
    ref = resolve_pipeline_task_ref(mysql, student_id="alice", task_id="task_abc")
    assert ref["status"] == "needs_input"
    assert ref["resource_id"] == "task_abc"
    assert len(ref["result_version"]) == 64
    with pytest.raises(LookupError):
        resolve_pipeline_task_ref(mysql, student_id="bob", task_id="task_abc")
    with pytest.raises(LookupError):
        resolve_pipeline_task_ref(mysql, student_id="alice", task_id="task_fake")


def test_completed_portrait_requires_owned_row_and_exact_payload():
    portrait = {"source_confirmation_task_id": "task_old"}
    result = {"task_id": "task_abc", "student_id": "alice", "status": "completed",
              "resource_id": "7", "portrait_id": "7", "portrait": portrait}
    mysql = FakeMysql({"task_abc": task(status="completed", result=result,
                                          pipeline="L2_generate_portrait")})
    with pytest.raises(ValueError):
        resolve_pipeline_task_ref(mysql, student_id="alice", task_id="task_abc")
    mysql.portraits["7"] = {"id": 7, "student_id": "alice", "portrait_json": json.dumps(portrait)}
    assert resolve_pipeline_task_ref(mysql, student_id="alice", task_id="task_abc")["resource_id"] == "7"
    mysql.portraits["7"]["portrait_json"] = json.dumps({"fake": True})
    with pytest.raises(ValueError):
        resolve_pipeline_task_ref(mysql, student_id="alice", task_id="task_abc")


def test_completed_status_cannot_be_synthesized_from_task_row_alone():
    mysql = FakeMysql({"task_abc": task(status="completed", result={
        "task_id": "task_abc", "student_id": "alice", "status": "partial",
    })})
    with pytest.raises(ValueError, match="完成状态"):
        resolve_pipeline_task_ref(mysql, student_id="alice", task_id="task_abc")


def test_only_completed_verified_l0_l2_receive_canonical_resource_family(monkeypatch):
    profile = {"_resume_confirmation": {"task_id": "task_abc"}}
    monkeypatch.setattr("app.services.pipeline.require_confirmed_profile", lambda _mysql, _sid: profile)
    l0 = task(status="completed", result={
        "task_id": "task_abc", "student_id": "alice", "status": "completed", "confirmed": True,
    })
    resume_ref = resolve_pipeline_task_ref(
        FakeMysql({"task_abc": l0}), student_id="alice", task_id="task_abc")
    assert resume_ref["canonical_resource_uri"] == "career://me/resume"

    portrait = {"D2_skill": {"dimension_score": 80}}
    l2 = task(status="completed", result={
        "task_id": "task_abc", "student_id": "alice", "status": "completed",
        "resource_id": "7", "portrait_id": "7", "portrait": portrait,
    }, pipeline="L2_generate_portrait")
    mysql = FakeMysql({"task_abc": l2}, {"7": {
        "id": 7, "student_id": "alice", "portrait_json": json.dumps(portrait),
    }})
    portrait_ref = resolve_pipeline_task_ref(mysql, student_id="alice", task_id="task_abc")
    assert portrait_ref["canonical_resource_uri"] == "career://me/portrait"

    partial_ref = resolve_pipeline_task_ref(
        FakeMysql({"task_abc": task(status="partial", pipeline="L2_generate_portrait")}),
        student_id="alice", task_id="task_abc")
    assert "canonical_resource_uri" not in partial_ref

    monkeypatch.setattr("app.services.pipeline.require_confirmed_profile", lambda _mysql, _sid: {
        "_resume_confirmation": {"task_id": "task_new"}})
    with pytest.raises(ValueError, match="确认版本与任务不一致"):
        resolve_pipeline_task_ref(FakeMysql({"task_abc": l0}), student_id="alice", task_id="task_abc")


def test_prepare_turn_binds_task_only_to_exact_current_resource_version(monkeypatch):
    profile = {"_resume_confirmation": {"task_id": "task_abc"}}
    monkeypatch.setattr("app.services.pipeline.require_confirmed_profile", lambda _mysql, _sid: profile)
    mysql = FakeMysql({"task_abc": task(status="completed", result={
        "task_id": "task_abc", "student_id": "alice", "status": "completed", "confirmed": True,
    })})
    monkeypatch.setattr("app.services.db.get_mysql_client", lambda: mysql)
    current = {"version": "task_abc", "status": "completed", "uri": "career://me/resume"}
    monkeypatch.setattr("app.services.agent_runtime.resource_catalog.list_platform_resources",
                        lambda _mysql, *, student_id: {"resources": [current]})

    prepared = prepare_user_turn("列出项目", student_id="alice",
        content_parts=[{"type": "artifact_ref", "task_id": "task_abc"}])
    assert prepared["verified_resource_refs"] == [
        {"uri": "career://me/resume", "version": "task_abc"}]

    # If the authoritative current version changed, never substitute it for
    # the referenced historical task; the status-only ref forces evidence
    # retrieval to fail closed instead of selecting from the current catalog.
    current["version"] = "task_new"
    stale = prepare_user_turn("列出项目", student_id="alice",
        content_parts=[{"type": "artifact_ref", "task_id": "task_abc"}])
    assert stale["verified_resource_refs"] == [{
        "task_id": "task_abc", "status": "reference_not_current", "task_status": "completed"}]


def test_prepare_turn_keeps_partial_portrait_as_status_only(monkeypatch):
    mysql = FakeMysql({"task_abc": task(status="partial", pipeline="L2_generate_portrait")})
    monkeypatch.setattr("app.services.db.get_mysql_client", lambda: mysql)
    monkeypatch.setattr("app.services.agent_runtime.resource_catalog.list_platform_resources",
                        lambda *_args, **_kwargs: pytest.fail("partial task must not map to portrait resource"))
    prepared = prepare_user_turn("讨论画像任务", student_id="alice",
        content_parts=[{"type": "artifact_ref", "task_id": "task_abc"}])
    assert prepared["verified_task_refs"][0]["business_artifact_verified"] is False
    assert "career://me/portrait" not in str(prepared["verified_resource_refs"])
    assert prepared["verified_resource_refs"][0] == {
        "task_id": "task_abc", "status": "reference_not_current", "task_status": "partial"}


def test_prepare_turn_binds_completed_portrait_only_to_current_portrait_version(monkeypatch):
    portrait = {"D2_skill": {"dimension_score": 80}}
    mysql = FakeMysql({"task_abc": task(status="completed", pipeline="L2_generate_portrait", result={
        "task_id": "task_abc", "student_id": "alice", "status": "completed",
        "resource_id": "7", "portrait_id": "7", "portrait": portrait,
    })}, {"7": {"id": 7, "student_id": "alice", "portrait_json": json.dumps(portrait)}})
    monkeypatch.setattr("app.services.db.get_mysql_client", lambda: mysql)
    catalog = {"resources": [{"uri": "career://me/portrait", "version": "task_abc", "status": "completed"}]}
    monkeypatch.setattr("app.services.agent_runtime.resource_catalog.list_platform_resources",
                        lambda *_args, **_kwargs: catalog)
    prepared = prepare_user_turn("看一下画像证据", student_id="alice",
        content_parts=[{"type": "artifact_ref", "task_id": "task_abc"}])
    assert prepared["verified_resource_refs"] == [
        {"uri": "career://me/portrait", "version": "task_abc"}]

    catalog["resources"][0]["version"] = "task_new"
    stale = prepare_user_turn("看一下画像证据", student_id="alice",
        content_parts=[{"type": "artifact_ref", "task_id": "task_abc"}])
    assert stale["verified_resource_refs"] == [{
        "task_id": "task_abc", "status": "reference_not_current", "task_status": "completed"}]


def test_prepare_turn_discards_browser_summary_and_rejects_cross_owner():
    mysql = FakeMysql({"task_abc": task()})
    with patch("app.services.db.get_mysql_client", return_value=mysql):
        prepared = prepare_user_turn("帮我看看", student_id="alice", session_id="s",
            content_parts=[{"type": "artifact_ref", "task_id": "task_abc",
                            "summary": "浏览器伪造：画像完成", "status": "completed"}])
        assert "浏览器伪造" not in str(prepared)
        assert "task_abc" in prepared["attach_card"]
        with pytest.raises(LookupError):
            prepare_user_turn("", student_id="bob", session_id="s",
                content_parts=[{"type": "artifact_ref", "task_id": "task_abc"}])
