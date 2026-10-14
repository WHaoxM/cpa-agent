"""Operator-published, immutable job snapshots; never trust request-body flags.

This is a domain repository, not an Agent runtime. Publication is deliberately
available only to the operator CLI, not a model tool or public HTTP endpoint.
"""
import hashlib
import json
from datetime import datetime, timezone
from urllib.parse import urlparse


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def content_sha256(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def validate_snapshot(snapshot):
    if not isinstance(snapshot, dict):
        raise ValueError("岗位发布内容必须是对象")
    for field in ("position_name", "source_uri", "source_text", "reviewed_by", "reviewed_at",
                  "source_fetched_at", "source_license"):
        if not isinstance(snapshot.get(field), str) or not snapshot[field].strip():
            raise ValueError(f"岗位发布缺少 {field}")
    uri = urlparse(snapshot["source_uri"])
    if uri.scheme not in ("http", "https") or not uri.netloc:
        raise ValueError("岗位发布必须记录可核验的 HTTP(S) 来源 URI")
    for field in ("source_fetched_at", "reviewed_at"):
        at = datetime.fromisoformat(snapshot[field].replace("Z", "+00:00"))
        if at.tzinfo is None or at > datetime.now(timezone.utc):
            raise ValueError(f"{field} 必须是已发生的带时区时间")
    if datetime.fromisoformat(snapshot["reviewed_at"].replace("Z", "+00:00")) < datetime.fromisoformat(
            snapshot["source_fetched_at"].replace("Z", "+00:00")):
        raise ValueError("审核时间不得早于来源获取时间")
    if snapshot.get("source_text_sha256") != hashlib.sha256(snapshot["source_text"].encode("utf-8")).hexdigest():
        raise ValueError("岗位来源文本 SHA-256 不一致")
    if snapshot.get("review_state") not in {"approved", "pending"}:
        raise ValueError("岗位必须显式记录 review_state=approved|pending")
    skills = snapshot.get("required_skills")
    if not isinstance(skills, list) or any(
        not isinstance(s, dict) or not isinstance(s.get("name"), str) or not s["name"].strip()
        for s in skills
    ):
        raise ValueError("required_skills 必须是对象列表")
    profile = snapshot.get("job_profile")
    if not isinstance(profile, dict) or not {"degree_requirement", "major_requirements", "major_strict", "required_certs"}.issubset(profile):
        raise ValueError("岗位发布必须显式填写硬约束，不能使用推断默认值")
    if (profile["degree_requirement"] is not None and not isinstance(profile["degree_requirement"], str)
            or not isinstance(profile["major_strict"], bool)
            or any(not isinstance(profile[k], list) or any(not isinstance(v, str) for v in profile[k])
                   for k in ("major_requirements", "required_certs"))):
        raise ValueError("岗位硬约束类型不正确")
    # Empty/unknown facts are publishable as source facts, but must never enter
    # the scorer: its legacy D1 path treats an empty degree as "no requirement".
    evidence = snapshot.get("field_evidence")
    if not isinstance(evidence, dict):
        raise ValueError("岗位发布缺少逐字段来源证据")
    fields = ["position_name", "degree_requirement", "major_requirements", "major_strict",
              "required_certs", "required_skills"]
    for field in fields:
        item = evidence.get(field)
        if not isinstance(item, dict) or item.get("status") not in {"explicit", "not_stated"}:
            raise ValueError(f"岗位 {field} 缺少证据状态")
        excerpt = item.get("excerpt")
        if item["status"] == "explicit":
            if not isinstance(excerpt, str) or not excerpt.strip() or excerpt not in snapshot["source_text"]:
                raise ValueError(f"岗位 {field} 证据不在来源文本中")
        elif excerpt not in (None, ""):
            raise ValueError(f"岗位 {field} 未声明时不得伪造引文")
    if evidence["position_name"]["status"] != "explicit":
        raise ValueError("岗位名称必须有来源引文")
    if profile["degree_requirement"] and evidence["degree_requirement"]["status"] != "explicit":
        raise ValueError("学历硬约束不得无来源赋值")
    if profile["major_strict"] and evidence["major_strict"]["status"] != "explicit":
        raise ValueError("严格专业硬约束不得无来源赋值")
    if skills and evidence["required_skills"]["status"] != "explicit":
        raise ValueError("技能要求不得无来源赋值")
    return snapshot


def scoring_readiness(snapshot):
    """Conservative source completeness gate, separate from publication trust."""
    missing = []
    profile = snapshot["job_profile"]
    for field in ("degree_requirement", "major_requirements", "major_strict", "required_certs", "required_skills"):
        if snapshot["field_evidence"][field]["status"] != "explicit":
            missing.append(field)
    if not profile["degree_requirement"]:
        missing.append("degree_requirement_value")
    if not snapshot["required_skills"]:
        missing.append("required_skills_value")
    return list(dict.fromkeys(missing))


def publish_job(mysql, *, job_portrait_id, version, snapshot):
    """Called by a trusted operator after reviewing the source, never by an LLM."""
    if isinstance(job_portrait_id, str) and job_portrait_id.startswith("source_csb_"):
        raise ValueError("source_csb_ 是仅供来源展示的保留岗位 ID 前缀")
    validate_snapshot(snapshot)
    if snapshot["review_state"] != "approved":
        raise ValueError("岗位来源尚未经人工批准，不能发布为可信目录")
    if not isinstance(job_portrait_id, str) or not job_portrait_id or len(job_portrait_id) > 64:
        raise ValueError("job_portrait_id 无效")
    if not isinstance(version, str) or not version or len(version) > 64:
        raise ValueError("version 无效")
    digest = content_sha256(snapshot)
    # No UPSERT: publication versions are append-only and cannot be relabelled.
    mysql.execute(
        "INSERT INTO job_catalog_publication (job_portrait_id, publication_version, snapshot_json, "
        "content_sha256, status, published_at) VALUES (:jid, :version, :snapshot, :digest, 'published', NOW())",
        {"jid": job_portrait_id, "version": version, "snapshot": canonical_json(snapshot), "digest": digest},
        commit=True)
    result = read_published_job(mysql, job_portrait_id, version)
    if not result or result["content_sha256"] != digest:
        raise RuntimeError("岗位发布写后回读失败")
    return result


def read_published_job(mysql, job_portrait_id, version=None):
    # A source-only catalog ID can never be consumed as a scored publication,
    # including by direct service callers that bypass the HTTP input guard.
    if isinstance(job_portrait_id, str) and job_portrait_id.startswith("source_csb_"):
        return None
    from sqlalchemy.exc import ProgrammingError
    try:
        rows = mysql.fetch_all(
            "SELECT id, job_portrait_id, publication_version, snapshot_json, content_sha256, status "
            "FROM job_catalog_publication WHERE job_portrait_id=:jid "
            + ("AND publication_version=:version " if version else "")
            + "ORDER BY id DESC LIMIT 1", {"jid": job_portrait_id, "version": version})
    except ProgrammingError as exc:
        # Existing deployments may not yet have applied this additive migration.
        if getattr(exc.orig, "args", (None,))[0] == 1146:
            return None
        raise
    if not rows or rows[0].get("status") != "published":
        return None
    row = rows[0]
    snapshot = row["snapshot_json"]
    snapshot = json.loads(snapshot) if isinstance(snapshot, str) else snapshot
    validate_snapshot(snapshot)
    if snapshot["review_state"] != "approved":
        raise ValueError("已发布岗位缺少人工批准状态")
    if row["content_sha256"] != content_sha256(snapshot):
        raise ValueError("岗位发布内容摘要不一致")
    return {**row, "snapshot_json": snapshot}
