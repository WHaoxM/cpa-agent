"""Project user-reviewed equivalent links into the owner-private Neo4j partition.

The source is the append-only MySQL review journal. Neither model decisions nor
user notes become public ontology writes. Related proposals are not projected.
"""

from __future__ import annotations

import hashlib
import json

from sqlalchemy import text

from .personal_graph import PersonalGraphSuperseded, confirmed_projection, publish_confirmed_personal_graph
from .pipeline_events import ConfirmationTransaction
from .public_skill_graph import SOURCE_ID as PUBLIC_SKILL_SOURCE_ID


class _LockedConfirmation(ConfirmationTransaction):
    def fetch_all(self, query, parameters=None):
        return self.execute(query + " FOR UPDATE", parameters)


def _json(value):
    return json.loads(value) if isinstance(value, str) else value


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def _current_review(conn, owner, task_id, expected_revision):
    # Called after student_profile lock and require_confirmed_profile's task lock.
    from .pipeline import require_confirmed_profile

    profile = require_confirmed_profile(_LockedConfirmation(conn), owner)
    source = profile.get("_resume_confirmation", {}).get("task_id")
    if source != task_id:
        raise PersonalGraphSuperseded("实体审阅来源已被新确认简历替代")
    linking = profile.get("entity_linking")
    if not isinstance(linking, dict):
        raise ValueError("已确认简历没有实体提议")
    row = conn.execute(text(
        "SELECT revision, source_sha256, snapshot_json FROM resume_entity_review "
        "WHERE student_id=:sid AND source_confirmation_task_id=:tid "
        "ORDER BY revision DESC LIMIT 1 FOR UPDATE"
    ), {"sid": owner, "tid": task_id}).mappings().first()
    if row is None or row["revision"] != expected_revision:
        raise PersonalGraphSuperseded("实体审阅版本已变化")
    if row["source_sha256"] != linking.get("source_sha256"):
        raise PersonalGraphSuperseded("实体审阅原文来源已变化")
    return linking, _json(row["snapshot_json"])


def _approved_equivalents(linking, reviews):
    if not isinstance(reviews, list) or len(reviews) > 100:
        raise ValueError("审阅快照超出边界")
    mentions, decisions, candidates = (linking.get(key) for key in
                                       ("mentions", "decisions", "candidates"))
    if not all(isinstance(value, list) for value in (mentions, decisions, candidates)):
        raise ValueError("实体提议结构无效")
    by_index = {d.get("mention_index"): d for d in decisions if isinstance(d, dict)}
    approved = []
    for item in reviews:
        if not isinstance(item, dict) or item.get("action") != "accept" or item.get("relation") != "equivalent":
            continue
        index = item.get("mention_index")
        if type(index) is not int or not 0 <= index < len(mentions):
            raise ValueError("审阅索引与确认版本不符")
        proposal = by_index.get(index)
        candidate_index = item.get("candidate_index")
        if (not isinstance(proposal, dict) or proposal.get("decision") != "equivalent"
                or proposal.get("candidate_index") != candidate_index
                or type(candidate_index) is not int or not 0 <= candidate_index < len(candidates)):
            raise ValueError("审阅结果与服务端提议不符")
        candidate, mention = candidates[candidate_index], mentions[index]
        if not isinstance(candidate, dict) or candidate.get("source_id") != PUBLIC_SKILL_SOURCE_ID:
            raise ValueError("审阅候选不是已发布公共技能来源")
        name, evidence = candidate.get("name"), mention.get("evidence") if isinstance(mention, dict) else None
        mention_name = mention.get("name") if isinstance(mention, dict) else None
        if (not isinstance(name, str) or not 1 <= len(name) <= 160
                or not isinstance(mention_name, str) or not mention_name.strip()
                or not isinstance(evidence, str) or not evidence.strip()):
            raise ValueError("审阅提议缺少可信名称或来源证据")
        approved.append({"mention_index": index, "mention_name": mention_name,
                         "evidence": evidence, "public_skill_name": name})
    if len({row["mention_index"] for row in approved}) != len(approved):
        raise ValueError("审阅快照包含重复实体")
    return approved


def publish_reviewed_entity_links(mysql, client, *, owner_student_id, task_id, revision):
    """Idempotent replacement of links for one current confirmed snapshot/revision.

    Profile→task row locks serialize against a new confirmation and review POST.
    Neo4j's official driver executes all replacement and integrity checks in one
    transaction. Lost acknowledgement can safely replay the same revision.
    """
    if type(revision) is not int or revision < 1 or not isinstance(owner_student_id, str):
        raise ValueError("无效审阅投影来源")
    observed = mysql.fetch_all(
        "SELECT revision FROM resume_entity_review WHERE student_id=:sid "
        "AND source_confirmation_task_id=:tid ORDER BY revision DESC LIMIT 1",
        {"sid": owner_student_id, "tid": task_id})
    if not observed or observed[0]["revision"] != revision:
        raise PersonalGraphSuperseded("实体审阅版本已变化")
    # Ensure the base private snapshot exists before acquiring the locks below;
    # that publisher uses the same locks on its own SQL connection.
    base = publish_confirmed_personal_graph(mysql, client, owner_student_id=owner_student_id,
                                            expected_source_task_id=task_id)
    with mysql.engine.begin() as conn:
        row = conn.execute(text(
            "SELECT student_id FROM student_profile WHERE student_id=:sid FOR UPDATE"
        ), {"sid": owner_student_id}).first()
        if row is None:
            raise PersonalGraphSuperseded("当前确认简历不存在")
        projection = confirmed_projection(_LockedConfirmation(conn), owner_student_id)
        if projection["source_task_id"] != task_id or projection["snapshot_id"] != base["snapshot_id"]:
            raise PersonalGraphSuperseded("个人图谱快照已被新简历替代")
        linking, reviews = _current_review(conn, owner_student_id, task_id, revision)
        approved = _approved_equivalents(linking, reviews)
        params = {**projection, "revision": revision, "review_sha256": _digest(reviews),
                  "resume_source_sha256": linking["source_sha256"],
                  "approved": approved, "public_source_id": PUBLIC_SKILL_SOURCE_ID}

        def write(tx):
            # Lock deterministic snapshot before inspecting or replacing edges.
            row = tx.run(
                "MATCH (p:PersonalGraphSnapshot {snapshot_id:$snapshot_id}) "
                "WHERE labels(p)=['PersonalGraphSnapshot'] AND p.owner_student_id=$owner_student_id "
                "AND p.source_id=$source_id AND p.source_task_id=$source_task_id "
                "AND p.content_sha256=$content_sha256 AND p.state='verified' AND p.visibility='private' "
                "SET p.review_lock_version=coalesce(p.review_lock_version,0)+1 "
                "RETURN count(p) AS count", params).single()
            if row is None or row["count"] != 1:
                raise ValueError("当前个人图谱快照未通过来源校验")
            public = list(tx.run(
                "UNWIND $approved AS item "
                "OPTIONAL MATCH (s:PublicSkill {name:item.public_skill_name}) "
                "WHERE labels(s)=['PublicSkill'] AND s.source_id=$public_source_id "
                "AND s.visibility='public' "
                "RETURN item.mention_index AS idx, count(s) AS count", params))
            if len(public) != len(approved) or any(r["count"] != 1 for r in public):
                raise ValueError("已审阅公共技能不再唯一或尚未发布")
            conflicts = tx.run(
                "MATCH (p:PersonalGraphSnapshot {snapshot_id:$snapshot_id})"
                "-[:HAS_REVIEWED_ENTITY]->(m) "
                "WHERE labels(m)<>['PersonalEntityMention'] OR m.owner_student_id IS NULL "
                "OR m.owner_student_id<>$owner_student_id "
                "RETURN count(m) AS count", params).single()
            if conflicts and conflicts["count"]:
                raise ValueError("个人实体审阅节点归属冲突")
            tx.run(
                "MATCH (p:PersonalGraphSnapshot {snapshot_id:$snapshot_id})"
                "-[:HAS_REVIEWED_ENTITY]->(m:PersonalEntityMention) DETACH DELETE m", params).consume()
            tx.run(
                "MATCH (p:PersonalGraphSnapshot {snapshot_id:$snapshot_id}) "
                "UNWIND $approved AS item "
                "MATCH (s:PublicSkill {name:item.public_skill_name}) "
                "WHERE labels(s)=['PublicSkill'] AND s.source_id=$public_source_id AND s.visibility='public' "
                "CREATE (m:PersonalEntityMention {owner_student_id:$owner_student_id, "
                "snapshot_id:$snapshot_id, source_task_id:$source_task_id, "
                "source_sha256:$resume_source_sha256, review_sha256:$review_sha256, "
                "revision:$revision, mention_index:item.mention_index, "
                "name:item.mention_name, evidence:item.evidence, visibility:'private'}) "
                "CREATE (p)-[:HAS_REVIEWED_ENTITY {owner_student_id:$owner_student_id, "
                "snapshot_id:$snapshot_id, revision:$revision}]->(m) "
                "CREATE (m)-[:REVIEWED_EQUIVALENT {owner_student_id:$owner_student_id, "
                "snapshot_id:$snapshot_id, revision:$revision, source_id:$public_source_id}]->(s)", params).consume()
            count = tx.run(
                "MATCH (p:PersonalGraphSnapshot {snapshot_id:$snapshot_id})"
                "-[:HAS_REVIEWED_ENTITY]->(m:PersonalEntityMention)"
                "-[:REVIEWED_EQUIVALENT]->(s:PublicSkill) "
                "WHERE p.owner_student_id=$owner_student_id AND m.owner_student_id=$owner_student_id "
                "AND m.revision=$revision AND m.review_sha256=$review_sha256 "
                "AND m.source_sha256=$resume_source_sha256 "
                "AND labels(s)=['PublicSkill'] AND s.source_id=$public_source_id "
                "RETURN count(m) AS count", params).single()
            if count is None or count["count"] != len(approved):
                raise ValueError("个人实体审阅图谱回读不完整，已回滚")
            tx.run("MATCH (p:PersonalGraphSnapshot {snapshot_id:$snapshot_id}) "
                   "SET p.review_revision=$revision, p.review_sha256=$review_sha256", params).consume()

        with client.session() as session:
            session.execute_write(write)
    return {"status": "completed", "source_task_id": task_id, "revision": revision,
            "snapshot_id": projection["snapshot_id"], "equivalent_link_count": len(approved),
            "review_sha256": params["review_sha256"], "retryable": False}


def query_reviewed_entity_links(client, *, owner_student_id, mysql=None):
    """Read only current owner+confirmation+review revision, never legacy graph."""
    from .db import get_mysql_client
    from .pipeline import require_confirmed_profile
    from .personal_entity_link_jobs import projection_state

    unavailable = {"success": False, "status": "unavailable", "scope": "personal",
                   "rows": [], "message": "当前确认版本的人工审阅实体尚无完整个人图投影"}
    if not isinstance(owner_student_id, str) or not owner_student_id.strip():
        raise ValueError("missing trusted owner")
    mysql = mysql or get_mysql_client()
    try:
        projection = confirmed_projection(mysql, owner_student_id)
        profile = require_confirmed_profile(mysql, owner_student_id)
        latest = mysql.fetch_all(
            "SELECT revision, source_sha256, snapshot_json FROM resume_entity_review "
            "WHERE student_id=:sid AND source_confirmation_task_id=:tid "
            "ORDER BY revision DESC LIMIT 1",
            {"sid": owner_student_id, "tid": projection["source_task_id"]})
        if not latest or latest[0]["source_sha256"] != profile.get("entity_linking", {}).get("source_sha256"):
            return unavailable
        revision = latest[0]["revision"]
        state = projection_state(mysql, student_id=owner_student_id,
            task_id=projection["source_task_id"], revision=revision)
        if (not state or state.get("status") != "completed"
                or state.get("snapshot_id") != projection["snapshot_id"]
                or state.get("review_sha256") != _digest(_json(latest[0]["snapshot_json"]))):
            return unavailable
        params = {**projection, "revision": revision,
                  "review_sha256": state["review_sha256"],
                  "resume_source_sha256": latest[0]["source_sha256"],
                  "public_source_id": PUBLIC_SKILL_SOURCE_ID}
        marker = client.execute_query(
            "MATCH (p:PersonalGraphSnapshot {snapshot_id:$snapshot_id}) "
            "WHERE labels(p)=['PersonalGraphSnapshot'] AND p.owner_student_id=$owner_student_id "
            "AND p.source_id=$source_id AND p.source_task_id=$source_task_id "
            "AND p.content_sha256=$content_sha256 AND p.state='verified' AND p.visibility='private' "
            "AND p.review_revision=$revision AND p.review_sha256=$review_sha256 "
            "RETURN count(p) AS count", params, read_only=True)
        if marker != [{"count": 1}]:
            return unavailable
        rows = client.execute_query(
            "MATCH (p:PersonalGraphSnapshot {snapshot_id:$snapshot_id})"
            "-[r:HAS_REVIEWED_ENTITY]->(m:PersonalEntityMention)"
            "-[e:REVIEWED_EQUIVALENT]->(s:PublicSkill) "
            "WHERE labels(p)=['PersonalGraphSnapshot'] AND labels(m)=['PersonalEntityMention'] "
            "AND labels(s)=['PublicSkill'] AND p.owner_student_id=$owner_student_id "
            "AND m.owner_student_id=$owner_student_id AND r.owner_student_id=$owner_student_id "
            "AND e.owner_student_id=$owner_student_id AND p.source_task_id=$source_task_id "
            "AND p.content_sha256=$content_sha256 AND m.snapshot_id=$snapshot_id "
            "AND r.snapshot_id=$snapshot_id AND e.snapshot_id=$snapshot_id "
            "AND m.revision=$revision AND r.revision=$revision AND e.revision=$revision "
            "AND m.source_sha256=$resume_source_sha256 AND m.review_sha256=$review_sha256 "
            "AND m.visibility='private' "
            "AND s.source_id=$public_source_id AND s.visibility='public' "
            "RETURN m.mention_index AS mention_index, m.name AS mention_name, "
            "m.evidence AS evidence, s.name AS canonical_name, s.category AS category "
            "ORDER BY mention_index", params, read_only=True)
        if len(rows) != state.get("equivalent_link_count") or len({r["mention_index"] for r in rows}) != len(rows):
            return unavailable
        if projection["snapshot_id"] != confirmed_projection(mysql, owner_student_id)["snapshot_id"]:
            return unavailable
        current_review = mysql.fetch_all(
            "SELECT revision, source_sha256, snapshot_json FROM resume_entity_review "
            "WHERE student_id=:sid AND source_confirmation_task_id=:tid "
            "ORDER BY revision DESC LIMIT 1",
            {"sid": owner_student_id, "tid": projection["source_task_id"]})
        if (not current_review or current_review[0]["revision"] != revision
                or current_review[0]["source_sha256"] != latest[0]["source_sha256"]
                or _digest(_json(current_review[0]["snapshot_json"])) != state["review_sha256"]):
            return unavailable
        return {"success": True, "status": "ready", "scope": "personal", "rows": rows,
                "source_task_id": projection["source_task_id"], "review_revision": revision,
                "snapshot_id": projection["snapshot_id"]}
    except (LookupError, ValueError):
        return unavailable
