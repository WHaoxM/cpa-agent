"""Owner-scoped, resume-only evidence report over the confirmed L0 snapshot.

This is a domain projection, not another extractor or a substitute for the
job-specific L4 report. It never manufactures match scores or market claims.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy

from pydantic import BaseModel, ConfigDict, Field

from config.prompt_loader import load_prompt

from .job_publication import canonical_json, content_sha256
from .pipeline import require_confirmed_profile


_GROUPS = (
    ("overview", "基本资料与求职意向", ("name", "phone", "email", "target_role", "grade")),
    ("education", "教育背景", ("school", "major", "degree", "gpa", "rank_percent")),
    ("experience", "项目与实践经历", ("projects", "internship_months", "leadership_roles")),
    ("capabilities", "技能与资质", ("skills", "certificates")),
    ("achievements", "竞赛与研究", ("competitions", "research")),
    ("semantic_evidence", "已提取的行为与能力证据", ("semantic_facts",)),
)
_DISPLAY = {
    "name": "姓名", "phone": "电话", "email": "邮箱", "target_role": "目标方向",
    "grade": "年级", "school": "学校", "major": "专业", "degree": "学历",
    "gpa": "绩点", "rank_percent": "排名百分位", "projects": "项目",
    "internship_months": "实习时长（月）", "leadership_roles": "任职经历",
    "skills": "技能", "certificates": "证书", "competitions": "竞赛",
    "research": "研究", "semantic_facts": "语义事实",
}
_META = {"_resume_confirmation", "source", "source_sha256", "raw_text_chars",
         "structure_path", "chunk_count", "error", "failed_offsets", "next_action",
         "semantic_fact_issues", "semantic_fact_conflicts", "semantic_fact_next_action",
         "skill_mentions", "entity_linking", "structure_calls", "entity_extraction_call",
         "entity_extraction_status", "entity_extraction_issues"}
_ROLE_PROMPT_VERSION = 4


class _Interpretation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    text: str = Field(min_length=1, max_length=500)
    evidence_ids: list[str] = Field(min_length=1)


class _Synthesis(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    summary: _Interpretation
    strengths: list[_Interpretation]
    questions: list[_Interpretation]


class _RoleDirection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    title: str = Field(min_length=2, max_length=100)
    evidence_ids: list[str] = Field(min_length=1)
    questions_to_verify: list[str]


class _RoleDirections(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    directions: list[_RoleDirection] = Field(min_length=1, max_length=4)


def _object(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return {}
    return value if isinstance(value, dict) else {}


def _present(value):
    return value is not None and value != "" and value != [] and value != {}


def _quote_candidates(value):
    if isinstance(value, dict):
        preferred = [value.get(k) for k in ("evidence", "name", "title", "desc", "description", "role")]
        return [str(v).strip() for v in preferred if isinstance(v, str) and v.strip()]
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    return []


def _evidence_refs(value, blocks):
    # Exact substring only. Semantic/page inference belongs to the extractor;
    # no page number is invented when a normalized value cannot be located.
    for quote in _quote_candidates(value):
        for block in blocks:
            body = block.get("text") or ""
            if quote in body and isinstance(block.get("page"), int):
                return [{"page": block["page"], "block_id": block.get("block_id"),
                         "quote": quote[:180]}]
    return []


def _item(key, value, index, blocks):
    fields = value if isinstance(value, dict) else {"value": value}
    label = _DISPLAY.get(key, key)
    if isinstance(value, dict):
        label = str(value.get("name") or value.get("title") or label)
    refs = _evidence_refs(value, blocks)
    return {"id": f"{key}_{index}", "label": label, "fields": fields,
            "evidence_refs": refs,
            "evidence_status": "page_located" if refs else "confirmed_profile_only"}


def _chapter(chapter_id, title, keys, profile, blocks):
    items = []
    for key in keys:
        value = profile.get(key)
        if not _present(value):
            continue
        if isinstance(value, list):
            items.extend(_item(key, entry, index, blocks) for index, entry in enumerate(value)
                         if _present(entry))
        elif key == "semantic_facts" and isinstance(value, dict):
            items.extend(_item(fact, entry, index, blocks) for index, (fact, entry) in enumerate(value.items())
                         if _present(entry))
        else:
            items.append(_item(key, value, 0, blocks))
    return {"id": chapter_id, "title": title, "items": items}


def _entity_review(profile, blocks):
    linking = _object(profile.get("entity_linking"))
    mentions = linking.get("mentions") if isinstance(linking.get("mentions"), list) else []
    candidates = linking.get("candidates") if isinstance(linking.get("candidates"), list) else []
    decisions = {d.get("mention_index"): d for d in linking.get("decisions", [])
                 if isinstance(d, dict) and isinstance(d.get("mention_index"), int)}
    items = []
    for index, mention in enumerate(mentions):
        if not isinstance(mention, dict):
            continue
        decision = decisions.get(index) or {}
        ci = decision.get("candidate_index")
        candidate = candidates[ci] if isinstance(ci, int) and 0 <= ci < len(candidates) else None
        fields = {"original_mention": mention.get("name"),
                  "decision": decision.get("decision") or "unresolved",
                  "candidate": candidate.get("name") if isinstance(candidate, dict) else None,
                  "rationale": decision.get("rationale"),
                  "review_status": "proposed_not_confirmed" if decision else "unresolved"}
        refs = _evidence_refs(mention.get("evidence") or mention.get("name"), blocks)
        items.append({"id": f"entity_link_{index}", "label": mention.get("name") or "技能原文",
                      "fields": fields, "evidence_refs": refs,
                      "evidence_status": "page_located" if refs else "confirmed_profile_only"})
    return {"id": "entity_review", "title": "技能实体归一化审阅", "items": items,
            "status": linking.get("status") or "not_available",
            "reason": linking.get("reason"),
            "notice": "归一化仅为候选建议，未自动改写已确认简历或公共图谱。"}


def build_resume_diagnostic_report(profile: dict, *, student_id: str) -> dict:
    """Build a complete report *within resume scope* from confirmed facts only."""
    confirmation = profile["_resume_confirmation"]
    evidence = _object(confirmation.get("source_evidence"))
    blocks = [block for block in evidence.get("blocks", []) if isinstance(block, dict)]
    covered = set()
    chapters = []
    for chapter_id, title, keys in _GROUPS:
        covered.update(keys)
        chapters.append(_chapter(chapter_id, title, keys, profile, blocks))
    chapters.append(_entity_review(profile, blocks))
    chapters.append({"id": "role_directions", "title": "候选岗位方向", "items": [],
                     "status": "pending_model_interpretation", "basis": "confirmed_resume_only",
                     "notice": "方向建议不是实时招聘岗位、正式匹配结果或胜任力评分。"})
    other = sorted(key for key, value in profile.items()
                   if key not in covered | _META and not key.startswith("_") and _present(value))
    chapters.append(_chapter("other_facts", "其他已提取事实", other, profile, blocks))
    missing_pages = evidence.get("missing_pages") or []
    limitations = ["岗位方向是基于已确认简历的待核验探索建议；不是实时招聘岗位、正式匹配分或市场需求判断。"]
    if missing_pages:
        limitations.append("原文件有未提取页面，本报告仅覆盖用户明确确认的已提取内容。")
    report_id = "resume_" + hashlib.sha256(
        f"{student_id}\x00{confirmation['task_id']}".encode("utf-8")).hexdigest()[:32]
    return {
        "schema_version": "resume-diagnostic-v1", "report_type": "resume_diagnostic",
        "report_id": report_id, "student_id": student_id,
        "status": "partial",
        "source_confirmation_task_id": confirmation["task_id"],
        "source_file_id": evidence.get("file_id"),
        "coverage": {key: evidence.get(key) for key in
                     ("page_count", "observed_pages", "missing_pages", "accuracy_verified")},
        "chapters": chapters, "analysis": None, "analysis_status": "not_generated",
        "quality_status": "not_assessed",
        "limitations": limitations,
        "missing_sections": (["unparsed_source_pages"] if missing_pages else []) +
                            ["evidence_grounded_semantic_analysis", "resume_based_role_directions"],
    }


def _synthesize(report: dict, *, llm_client=None) -> dict:
    facts = [{"id": item["id"], "label": item["label"], "fields": item["fields"],
              "evidence_refs": item["evidence_refs"]}
             for chapter in report["chapters"] if chapter["id"] not in {"overview", "entity_review", "role_directions"}
             for item in chapter["items"]]
    if not facts:
        report["analysis_status"] = "insufficient_evidence"
        return report
    payload = canonical_json(facts)
    if len(payload) > 24000:
        report["analysis_status"] = "input_budget_exceeded"
        return report
    try:
        from ..utils.llm_client import get_llm_client
        client = llm_client or get_llm_client()
        response = client.chat_with_metadata(
            messages=[
                {"role": "system", "content": load_prompt(
                    "report.resume_diagnostic.user", evidence_json="见下一条消息中的 JSON 数据")},
                {"role": "user", "content": payload},
            ],
            temperature=0.1, max_tokens=1500,
            response_format={"type": "json_schema", "json_schema": {
                "name": "resume_diagnostic_synthesis", "strict": True,
                "schema": _Synthesis.model_json_schema()}},
        )
        if response.finish_reason == "length" or response.context_status.fits is False:
            report["analysis_status"] = "model_output_truncated"
            return report
        analysis = _Synthesis.model_validate_json(response.text).model_dump()
        valid_ids = {fact["id"] for fact in facts}
        claims = [analysis["summary"], *analysis["strengths"], *analysis["questions"]]
        if any(not set(claim["evidence_ids"]) <= valid_ids for claim in claims):
            report["analysis_status"] = "invalid_evidence_references"
            return report
        report["analysis"] = analysis
        report["analysis_status"] = "model_interpretation_with_references"
        report["quality_status"] = "model_interpretation_unverified"
        report["model_usage"] = response.usage
        report["model_id"] = response.model
        report["missing_sections"].remove("evidence_grounded_semantic_analysis")
    except Exception:
        report["analysis_status"] = "model_unavailable_or_invalid"
    return report


def _synthesize_role_directions(report: dict, *, llm_client=None) -> dict:
    """Interpret confirmed resume facts; never turn model text into catalog authority."""
    chapter = next(ch for ch in report["chapters"] if ch["id"] == "role_directions")
    facts = [{"id": item["id"], "label": item["label"], "fields": item["fields"]}
             for ch in report["chapters"] if ch["id"] not in {"entity_review", "role_directions"}
             for item in ch["items"] if item["id"] not in {"name_0", "phone_0", "email_0"}]
    if not facts:
        chapter["status"] = "insufficient_resume_evidence"
        return report
    payload = canonical_json(facts)
    if len(payload) > 24000:
        chapter["status"] = "input_budget_exceeded"
        return report
    try:
        from ..utils.llm_client import get_llm_client
        client = llm_client or get_llm_client()
        response = client.chat_with_metadata(
            messages=[
                {"role": "system", "content": load_prompt(
                    "report.role_directions.user", evidence_json="见下一条消息中的 JSON 数据")},
                {"role": "user", "content": payload},
            ], temperature=0.1, max_tokens=1400,
            response_format={"type": "json_schema", "json_schema": {
                "name": "resume_role_directions", "strict": True,
                "schema": _RoleDirections.model_json_schema()}},
        )
        if response.finish_reason == "length" or response.context_status.fits is False:
            chapter["status"] = "model_output_truncated"
            return report
        directions = _RoleDirections.model_validate_json(response.text).directions
        valid_ids = {fact["id"] for fact in facts}
        if any(not set(direction.evidence_ids) <= valid_ids for direction in directions):
            chapter["status"] = "invalid_evidence_references"
            return report
        fact_by_id = {fact["id"]: fact for fact in facts}
        evidence_by_id = {
            item["id"]: item.get("evidence_refs", [])
            for source_chapter in report["chapters"]
            for item in source_chapter["items"]
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        def fact_label(fact_id: str) -> str:
            fact = fact_by_id[fact_id]
            fields = fact["fields"]
            value = fields.get("name") or fields.get("title") or fields.get("value")
            return str(value if isinstance(value, str) and value.strip() else fact["label"])[:70]
        chapter["items"] = [{
            "id": f"role_direction_{index}", "label": direction.title,
            "fields": {"title": direction.title,
                       "rationale": "依据简历记录的「" + "」「".join(
                           fact_label(fact_id) for fact_id in direction.evidence_ids[:3]
                       ) + "」列为探索方向；具体岗位要求与适合程度仍需核验。",
                       "evidence_ids": direction.evidence_ids,
                       "questions_to_verify": direction.questions_to_verify,
                       "source_type": "resume_model_inference",
                       "source_confirmation_task_id": report["source_confirmation_task_id"]},
            # Match the rationale's fact-ID order.  The UI shows the first
            # three citations, so chapter iteration order would otherwise
            # hide the evidence actually named in that rationale.
            "evidence_refs": [ref for fact_id in direction.evidence_ids
                              for ref in evidence_by_id.get(fact_id, [])],
            "evidence_status": "model_interpretation_with_fact_references",
        } for index, direction in enumerate(directions)]
        chapter["status"] = "model_interpretation_unverified"
        chapter["prompt_version"] = _ROLE_PROMPT_VERSION
        chapter["model_usage"] = response.usage
        chapter["model_id"] = response.model
        report["missing_sections"].remove("resume_based_role_directions")
    except Exception:
        chapter["status"] = "model_unavailable_or_invalid"
    if not report["missing_sections"]:
        report["status"] = "completed"
    return report


def _require_current(mysql, student_id):
    try:
        return require_confirmed_profile(mysql, student_id)
    except ValueError as error:
        raise ResumeReportNeedsInput(str(error)) from error


class ResumeReportNeedsInput(Exception):
    pass


class ResumeReportStale(Exception):
    pass


def create_or_read_resume_diagnostic(mysql, *, student_id: str, llm_client=None) -> dict:
    profile = _require_current(mysql, student_id)
    report = build_resume_diagnostic_report(profile, student_id=student_id)
    existing = mysql.fetch_all(
        "SELECT report_id, student_id, source_confirmation_task_id, status, report_json, content_sha256 "
        "FROM resume_diagnostic_report WHERE student_id=:sid AND source_confirmation_task_id=:tid LIMIT 1",
        {"sid": student_id, "tid": report["source_confirmation_task_id"]})
    existing_report = _object(existing[0].get("report_json")) if existing else {}
    existing_roles = next((chapter for chapter in existing_report.get("chapters", [])
                           if isinstance(chapter, dict) and chapter.get("id") == "role_directions"), {})
    if (existing and existing[0].get("status") == "completed"
            and existing_report.get("quality_status") == "model_interpretation_unverified"
            and existing_roles.get("status") == "model_interpretation_unverified"
            and existing_roles.get("prompt_version") == _ROLE_PROMPT_VERSION):
        return read_resume_diagnostic(mysql, student_id=student_id, report_id=report["report_id"])
    report = _synthesize(report, llm_client=llm_client)
    report = _synthesize_role_directions(report, llm_client=llm_client)
    mysql.execute(
        "INSERT INTO resume_diagnostic_report "
        "(report_id,student_id,source_confirmation_task_id,status,report_json,content_sha256) "
        "VALUES (:rid,:sid,:tid,:status,:payload,:sha) "
        "ON DUPLICATE KEY UPDATE status=VALUES(status),report_json=VALUES(report_json),"
        "content_sha256=VALUES(content_sha256)",
        {"rid": report["report_id"], "sid": student_id,
         "tid": report["source_confirmation_task_id"], "status": report["status"],
         "payload": canonical_json(report), "sha": content_sha256(report)}, commit=True)
    return read_resume_diagnostic(mysql, student_id=student_id, report_id=report["report_id"])


def read_resume_diagnostic(mysql, *, student_id: str, report_id: str | None = None) -> dict:
    if report_id:
        rows = mysql.fetch_all(
            "SELECT report_id,student_id,source_confirmation_task_id,status,report_json,content_sha256 "
            "FROM resume_diagnostic_report WHERE student_id=:sid AND report_id=:rid LIMIT 1",
            {"sid": student_id, "rid": report_id})
        if not rows:
            raise LookupError("当前简历报告不存在")
    profile = _require_current(mysql, student_id)
    confirmation_id = profile["_resume_confirmation"]["task_id"]
    if not report_id:
        rows = mysql.fetch_all(
            "SELECT report_id,student_id,source_confirmation_task_id,status,report_json,content_sha256 "
            "FROM resume_diagnostic_report WHERE student_id=:sid AND source_confirmation_task_id=:tid LIMIT 1",
            {"sid": student_id, "tid": confirmation_id})
    if not rows:
        raise LookupError("当前简历报告不存在")
    row = rows[0]
    report = _object(row.get("report_json"))
    if row.get("source_confirmation_task_id") != confirmation_id:
        raise ResumeReportStale("报告源于旧版已确认简历，请重新生成")
    expected = build_resume_diagnostic_report(profile, student_id=student_id)
    invariant_keys = ("schema_version", "report_type", "report_id", "student_id",
                      "source_confirmation_task_id", "source_file_id", "coverage")
    factual_chapters = [chapter for chapter in report.get("chapters", [])
                        if isinstance(chapter, dict) and chapter.get("id") != "role_directions"]
    expected_factual_chapters = [chapter for chapter in expected["chapters"]
                                 if chapter["id"] != "role_directions"]
    role_chapters = [chapter for chapter in report.get("chapters", [])
                     if isinstance(chapter, dict) and chapter.get("id") == "role_directions"]
    legacy_without_directions = not role_chapters and report.get("schema_version") == "resume-diagnostic-v1"
    if (row.get("student_id") != student_id or row.get("report_id") != expected["report_id"]
            or row.get("status") != report.get("status")
            or any(report.get(key) != expected.get(key) for key in invariant_keys)
            or factual_chapters != expected_factual_chapters
            or (len(role_chapters) != 1 and not legacy_without_directions)
            or row.get("content_sha256") != content_sha256(report)):
        raise ResumeReportStale("报告内容或来源已变化，请重新核对简历")
    # The published-job overlay is read afresh and never persisted into the
    # resume report hash: job updates must not invalidate confirmed resume facts.
    result = deepcopy(report)
    result["resume_confirmed_at"] = profile["_resume_confirmation"].get("confirmed_at")
    if legacy_without_directions:
        result["chapters"].append({"id": "role_directions", "title": "候选岗位方向",
                                   "items": [], "status": "pending_upgrade",
                                   "basis": "confirmed_resume_only",
                                   "notice": "当前是旧版简历事实报告；请发起生成以补充基于简历的岗位方向。"})
        result["status"] = "partial"
        result["missing_sections"] = list(result.get("missing_sections") or []) + [
            "resume_based_role_directions"]
    try:
        from .job_catalog_listing import list_published_job_candidates
        result["job_catalog"] = list_published_job_candidates(mysql, limit=20, offset=0)
    except Exception:
        result["job_catalog"] = {"status": "unavailable", "total": 0, "items": [],
                                 "catalog_version": None, "catalog_latest_published_at": None,
                                 "has_more": False}
    return result
