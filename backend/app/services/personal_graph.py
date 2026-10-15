"""Read contract for verified, owner-scoped personal graph projections.

No legacy Person/Skill migration is implicit here. The legacy entity_id was
client-controlled and shared skill properties are mutable across writers.
Only the confirmed MySQL profile publisher creates this separate partition.
"""

from __future__ import annotations

import hashlib
import json

SOURCE_ID = "confirmed_student_profile_v1"
PAGE_SIZE = 10
MAX_SKILLS = 500


class PersonalGraphSuperseded(ValueError):
    """A durable projection request no longer matches the current confirmation."""


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def confirmed_projection(mysql, owner_student_id):
    """Use the existing authoritative confirmation validator, not caller data."""
    from .pipeline import require_confirmed_profile

    profile = require_confirmed_profile(mysql, owner_student_id)
    task = profile["_resume_confirmation"]["task_id"]
    candidate = {k: v for k, v in profile.items()
                 if k not in {"_resume_confirmation", "_user_self_report", "_skill_exploration"}}
    values = candidate.get("skills")
    if not isinstance(values, list) or len(values) > MAX_SKILLS:
        raise ValueError("已确认技能列表缺失或超过发布上限，请核对资料")
    skills = []
    for index, item in enumerate(values):
        name = item if isinstance(item, str) else item.get("name") if isinstance(item, dict) else None
        if not isinstance(name, str) or not name.strip() or len(name) > 160:
            raise ValueError("已确认技能中存在无效名称；未部分发布")
        skills.append({"skill_id": str(index), "name": name,
                       "evidence": f"已确认简历 skills[{index}]" + (".name" if isinstance(item, dict) else "")})
    content_sha256 = _digest(candidate)
    return {"owner_student_id": owner_student_id, "source_id": SOURCE_ID,
            "source_ref": "resume-confirmation:" + task, "source_task_id": task,
            "content_sha256": content_sha256,
            "snapshot_id": _digest([owner_student_id, task, content_sha256]), "skills": skills}


def _unavailable():
    return {"success": False, "status": "unavailable", "scope": "personal",
            "message": "个人图谱暂不可用：当前确认版本尚无完整且来源已核验的个人图谱快照。"
                       "历史 Person/Skill 缺少可信归属，不自动认领；不能据此判断你没有技能。"
                       "可通过 career_data_read 读取已确认简历/画像。"}

SNAPSHOT_QUERY = """
MATCH (p:PersonalGraphSnapshot)
WHERE labels(p) = ['PersonalGraphSnapshot']
  AND p.owner_student_id = $owner_student_id
  AND p.source_id = $source_id AND p.provenance_version = 1
  AND p.state = 'verified' AND p.visibility = 'private'
  AND p.source_ref IS NOT NULL AND p.source_ref <> ''
  AND p.snapshot_id IS NOT NULL AND p.snapshot_id <> ''
  AND p.snapshot_id = $snapshot_id AND p.content_sha256 = $content_sha256
  AND p.source_ref = $source_ref AND p.source_task_id = $source_task_id
RETURN p.snapshot_id AS snapshot_id, p.source_ref AS source_ref
LIMIT 2
"""

SKILLS_QUERY = """
MATCH (p:PersonalGraphSnapshot)-[r:HAS_PERSONAL_SKILL]->(s:PersonalSkill)
WHERE labels(p) = ['PersonalGraphSnapshot'] AND labels(s) = ['PersonalSkill']
  AND p.owner_student_id = $owner_student_id
  AND r.owner_student_id = $owner_student_id AND s.owner_student_id = $owner_student_id
  AND p.source_id = $source_id AND r.source_id = $source_id AND s.source_id = $source_id
  AND p.provenance_version = 1 AND r.provenance_version = 1 AND s.provenance_version = 1
  AND p.state = 'verified' AND p.visibility = 'private' AND s.visibility = 'private'
  AND p.content_sha256 = $content_sha256 AND p.source_task_id = $source_task_id
  AND r.content_sha256 = $content_sha256 AND s.content_sha256 = $content_sha256
  AND r.source_task_id = $source_task_id AND s.source_task_id = $source_task_id
  AND p.snapshot_id = $snapshot_id AND r.snapshot_id = $snapshot_id AND s.snapshot_id = $snapshot_id
  AND p.source_ref = $source_ref AND r.source_ref = $source_ref AND s.source_ref = $source_ref
  AND s.name IS NOT NULL AND s.skill_id IS NOT NULL AND s.skill_id <> ''
  AND s.evidence IS NOT NULL AND s.evidence <> ''
  AND ($entity_name = '' OR s.name = $entity_name)
RETURN s.name AS name, null AS category, s.evidence AS evidence,
       p.source_ref AS source_ref
ORDER BY s.name, s.skill_id
SKIP $offset LIMIT $fetch_limit
"""

INTEGRITY_QUERY = """
MATCH (p:PersonalGraphSnapshot {snapshot_id: $snapshot_id})
OPTIONAL MATCH (p)-[r:HAS_PERSONAL_SKILL]->(s)
RETURN count(r) AS edge_count,
       count(CASE WHEN labels(s) = ['PersonalSkill']
          AND r.owner_student_id = $owner_student_id AND s.owner_student_id = $owner_student_id
          AND r.source_id = $source_id AND s.source_id = $source_id
          AND r.provenance_version = 1 AND s.provenance_version = 1
          AND s.visibility = 'private'
          AND r.snapshot_id = $snapshot_id AND s.snapshot_id = $snapshot_id
          AND r.source_ref = $source_ref AND s.source_ref = $source_ref
          AND r.source_task_id = $source_task_id AND s.source_task_id = $source_task_id
          AND r.content_sha256 = $content_sha256 AND s.content_sha256 = $content_sha256
          AND any(expected IN $skills WHERE expected.skill_id = s.skill_id
                  AND expected.name = s.name AND expected.evidence = s.evidence)
          THEN s.skill_id END) AS valid_count,
       count(DISTINCT s.skill_id) AS unique_count
"""


def _integrity_ok(rows, expected):
    return len(rows) == 1 and all(rows[0].get(k) == expected for k in ("edge_count", "valid_count", "unique_count"))


def publish_confirmed_personal_graph(mysql, client, *, owner_student_id, expected_source_task_id=None):
    """Server-only publication. No profile, task ID or owner from model input.

    SQL row locks serialize source confirmation against Neo4j publication. The
    graph mutation itself is one retriable Neo4j transaction; an interrupted
    transaction rolls back, and a lost acknowledgement is safe to replay.
    """
    from sqlalchemy import text
    from .pipeline_events import ConfirmationTransaction

    class LockedConfirmation(ConfirmationTransaction):
        # require_confirmed_profile performs exactly two SELECTs. Locking reads
        # are current reads in MySQL REPEATABLE READ, unlike a stale consistent
        # snapshot established before the task row lock was acquired.
        def fetch_all(self, query, parameters=None):
            return self.execute(query + " FOR UPDATE", parameters)

    if not isinstance(owner_student_id, str) or not owner_student_id.strip():
        raise ValueError("missing trusted owner")
    # Fail rather than fall back to nontransactional publication for adapters
    # that cannot provide SQLAlchemy's actual transaction/row locks.
    with mysql.engine.begin() as conn:
        conn.execute(text("SELECT student_id FROM student_profile WHERE student_id=:sid FOR UPDATE"), {"sid": owner_student_id}).all()
        sql = LockedConfirmation(conn)
        projection = confirmed_projection(sql, owner_student_id)
        if expected_source_task_id is not None and projection["source_task_id"] != expected_source_task_id:
            raise PersonalGraphSuperseded("个人图谱投影请求已被新的确认版本替代")
        conn.execute(text("SELECT task_id FROM pipeline_task WHERE task_id=:tid AND student_id=:sid FOR UPDATE"),
                     {"tid": projection["source_task_id"], "sid": owner_student_id}).all()
        # Locking task follows the same profile->task ordering as confirmation.
        if projection != confirmed_projection(sql, owner_student_id):
            raise ValueError("确认来源发生变化，未发布")
        for ddl in (
            "CREATE CONSTRAINT personal_snapshot_id IF NOT EXISTS FOR (p:PersonalGraphSnapshot) REQUIRE p.snapshot_id IS UNIQUE",
            "CREATE CONSTRAINT personal_skill_identity IF NOT EXISTS FOR (s:PersonalSkill) REQUIRE (s.snapshot_id, s.skill_id) IS UNIQUE",
        ):
            client.execute_query(ddl, read_only=False)

        def write(tx):
            # Lock the deterministic snapshot before reading or repairing it.
            tx.run("MERGE (p:PersonalGraphSnapshot {snapshot_id: $snapshot_id}) "
                   "SET p.lock_version = coalesce(p.lock_version, 0) + 1", projection).consume()
            existing = list(tx.run("MATCH (p:PersonalGraphSnapshot {snapshot_id: $snapshot_id}) "
                                   "RETURN p.owner_student_id AS owner, labels(p) AS labels", projection))
            if existing[0]["owner"] not in (None, owner_student_id) or existing[0]["labels"] != ["PersonalGraphSnapshot"]:
                raise ValueError("快照归属冲突，未发布")
            conflicts = tx.run("MATCH (s:PersonalSkill {snapshot_id: $snapshot_id}) "
                               "WHERE labels(s) <> ['PersonalSkill'] OR (s.owner_student_id IS NOT NULL AND s.owner_student_id <> $owner_student_id) "
                               "RETURN count(s) AS count", projection).single()["count"]
            if conflicts:
                raise ValueError("技能快照归属冲突，未发布")
            # Replace this deterministic version completely, not patch a partial
            # result. Other versions remain private and cannot pass the read gate.
            tx.run("MATCH (s:PersonalSkill {snapshot_id: $snapshot_id}) DETACH DELETE s", projection).consume()
            tx.run("MATCH (p:PersonalGraphSnapshot {snapshot_id: $snapshot_id})-[r:HAS_PERSONAL_SKILL]->() DELETE r", projection).consume()
            tx.run("""
                MATCH (p:PersonalGraphSnapshot {snapshot_id: $snapshot_id})
                SET p.owner_student_id=$owner_student_id, p.source_id=$source_id,
                    p.source_ref=$source_ref, p.source_task_id=$source_task_id,
                    p.content_sha256=$content_sha256, p.provenance_version=1,
                    p.visibility='private', p.state='building', p.skill_count=size($skills)
                WITH p UNWIND $skills AS row
                CREATE (s:PersonalSkill {snapshot_id:$snapshot_id, skill_id:row.skill_id,
                    name:row.name, evidence:row.evidence, owner_student_id:$owner_student_id,
                    source_id:$source_id, source_ref:$source_ref, source_task_id:$source_task_id,
                    content_sha256:$content_sha256, provenance_version:1, visibility:'private'})
                CREATE (p)-[:HAS_PERSONAL_SKILL {snapshot_id:$snapshot_id,
                    owner_student_id:$owner_student_id, source_id:$source_id,
                    source_ref:$source_ref, source_task_id:$source_task_id,
                    content_sha256:$content_sha256, provenance_version:1}]->(s)
            """, projection).consume()
            if not _integrity_ok([dict(r) for r in tx.run(INTEGRITY_QUERY, projection)], len(projection["skills"])):
                raise ValueError("个人图谱事务内回读不完整，已回滚")
            tx.run("MATCH (p:PersonalGraphSnapshot {snapshot_id:$snapshot_id}) SET p.state='verified'", projection).consume()

        with client.session() as session:
            session.execute_write(write)
    return {"success": True, "status": "completed", "source_ref": projection["source_ref"],
            "snapshot_id": projection["snapshot_id"], "skill_count": len(projection["skills"])}


def query_personal_skills(client, *, owner_student_id: str, entity_name: str = "", offset: int = 0, mysql=None):
    """Owner must come from RequestScope, never from tool arguments.

    This function is a domain query, not an authorization adapter. Its sole tool
    caller resolves Principal first. Only the current MySQL-confirmed projection
    can authorize a graph snapshot; old verified markers alone are insufficient.
    """
    if not isinstance(owner_student_id, str) or not owner_student_id.strip():
        raise ValueError("missing trusted owner")
    if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 10000:
        raise ValueError("offset 必须是 0 至 10000 的整数")
    if mysql is None:
        from .db import get_mysql_client
        mysql = get_mysql_client()
    try:
        params = confirmed_projection(mysql, owner_student_id)
    except (ValueError, LookupError):
        return _unavailable()
    snapshots = client.execute_query(SNAPSHOT_QUERY, params, read_only=True)
    if len(snapshots) != 1:
        return _unavailable()
    if not _integrity_ok(client.execute_query(INTEGRITY_QUERY, params, read_only=True), len(params["skills"])):
        return _unavailable()
    snapshot = snapshots[0]
    params.update(snapshot_id=snapshot["snapshot_id"], source_ref=snapshot["source_ref"],
                  entity_name=entity_name, offset=offset, fetch_limit=PAGE_SIZE + 1)
    rows = client.execute_query(SKILLS_QUERY, params, read_only=True)
    try:
        if params["snapshot_id"] != confirmed_projection(mysql, owner_student_id)["snapshot_id"]:
            return _unavailable()
    except (ValueError, LookupError):
        return _unavailable()
    return {"success": True, "rows": rows[:PAGE_SIZE], "has_more": len(rows) > PAGE_SIZE,
            "next_offset": offset + PAGE_SIZE if len(rows) > PAGE_SIZE else None,
            "source_id": SOURCE_ID, "source_ref": snapshot["source_ref"], "scope": "personal"}
