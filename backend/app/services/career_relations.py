"""Deterministic public career relations derived from active curated pathways.

The relation is an undirected shared-skill reference. It is not a promotion,
transfer, fit, or eligibility prediction. Only canonical taxonomy identities
and their reviewed pathway evidence are used.
"""
from contextlib import contextmanager
from datetime import date, datetime
import hashlib
import json
from itertools import combinations

from sqlalchemy import text

from .career_pathway import read_pathway

ALGORITHM_VERSION = "shared-skill-jaccard/v1"
SOURCE_KIND = "mysql.career_pathway_revision+career_role_skill"
WEIGHT_DEFINITION = "shared canonical skill ids / union of canonical skill ids (Jaccard)"
_LOCK_NAME = "cpa.career_relations.compute"


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value):
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _json(value):
    return json.loads(value) if isinstance(value, str) else value


def load_active_pathways(mysql):
    """Read and validate the single active revision for each public role."""
    rows = mysql.execute(
        "SELECT role_id, revision, role_name, content_sha256 FROM career_pathway_revision "
        "WHERE status='active' ORDER BY role_id, revision"
    )
    seen = set()
    pathways = []
    for row in rows:
        role_id = row.get("role_id")
        if not isinstance(role_id, str) or not role_id or role_id in seen:
            raise RuntimeError("Active pathway role identities are missing or duplicated")
        seen.add(role_id)
        payload = read_pathway(mysql, role_id)
        if (payload is None or payload.get("revision") != row.get("revision")
                or payload.get("content_sha256") != row.get("content_sha256")
                or payload.get("role_name") != row.get("role_name")):
            raise RuntimeError("Active pathway changed while building the relation source set")
        skills = []
        skill_ids = set()
        for skill in payload.get("skills", []):
            skill_id = skill.get("skill_id")
            if type(skill_id) is not int or skill_id <= 0 or skill_id in skill_ids:
                raise RuntimeError("Active pathway has invalid canonical skill identities")
            skill_ids.add(skill_id)
            evidence = skill.get("evidence")
            if not isinstance(evidence, dict) or not evidence.get("url") or not evidence.get("note"):
                raise RuntimeError("Active pathway skill evidence is incomplete")
            skills.append({
                "skill_id": skill_id,
                "name": skill["name"],
                "evidence": {"url": evidence["url"], "note": evidence["note"]},
            })
        skills.sort(key=lambda value: value["skill_id"])
        pathways.append({
            "role_id": role_id,
            "role_name": row["role_name"],
            "revision": row["revision"],
            "content_sha256": row["content_sha256"],
            "skills": skills,
        })
    return pathways


def _source_manifest(pathways):
    return [{
        "role_id": role["role_id"],
        "revision": role["revision"],
        "content_sha256": role["content_sha256"],
        "skill_ids": [skill["skill_id"] for skill in role["skills"]],
    } for role in sorted(pathways, key=lambda value: value["role_id"])]


def _fingerprint_manifest(manifest):
    return _digest({
        "algorithm_version": ALGORITHM_VERSION,
        "source_kind": SOURCE_KIND,
        "pathways": manifest,
    })


def _source_fingerprint(pathways):
    return _fingerprint_manifest(_source_manifest(pathways))


def _build_nodes(roles):
    nodes = []
    skill_maps = {}
    for role in roles:
        skill_map = {}
        for skill in role.get("skills", []):
            skill_id = skill.get("skill_id")
            if type(skill_id) is not int or skill_id <= 0 or skill_id in skill_map:
                raise ValueError("Skills must use unique positive taxonomy ids")
            name = skill.get("name")
            evidence = skill.get("evidence")
            if not isinstance(name, str) or not name.strip() or not isinstance(evidence, dict):
                raise ValueError("Every graph skill requires its pathway evidence")
            if not isinstance(evidence.get("url"), str) or not evidence["url"].startswith("https://"):
                raise ValueError("Graph evidence must use an HTTPS source")
            if not isinstance(evidence.get("note"), str) or not evidence["note"].strip():
                raise ValueError("Graph evidence note is required")
            skill_map[skill_id] = {
                "skill_id": skill_id,
                "name": name,
                "evidence": {"url": evidence["url"], "note": evidence["note"]},
            }
        skill_maps[role["role_id"]] = skill_map
        nodes.append({
            "id": role["role_id"],
            "label": role["role_name"],
            "revision": role["revision"],
            "content_sha256": role["content_sha256"],
            "skill_count": len(skill_map),
            "skills": sorted(skill_map.values(), key=lambda value: (value["name"].casefold(), value["skill_id"])),
        })
    return nodes, skill_maps


def _make_edge(left, right, skill_maps, input_sha256):
    left_skills = skill_maps[left["role_id"]]
    right_skills = skill_maps[right["role_id"]]
    shared_ids = sorted(left_skills.keys() & right_skills.keys())
    if not shared_ids:
        return None
    if any(left_skills[skill_id]["name"] != right_skills[skill_id]["name"] for skill_id in shared_ids):
        raise RuntimeError("A canonical skill id has conflicting names across active pathways")
    union_count = len(left_skills.keys() | right_skills.keys())
    shared = [{
        "skill_id": skill_id,
        "name": left_skills[skill_id]["name"],
        "source_evidence": left_skills[skill_id]["evidence"],
        "target_evidence": right_skills[skill_id]["evidence"],
    } for skill_id in shared_ids]
    edge = {
        "source": left["role_id"],
        "target": right["role_id"],
        "relation_type": "shared_skill",
        "weight": round(len(shared_ids) / union_count, 8),
        "shared_skill_count": len(shared_ids),
        "union_skill_count": union_count,
        "shared_skills": shared,
        "source_revision": left["revision"],
        "source_sha256": left["content_sha256"],
        "target_revision": right["revision"],
        "target_sha256": right["content_sha256"],
    }
    edge["id"] = _digest({
        "algorithm_version": ALGORITHM_VERSION,
        "source_fingerprint": input_sha256,
        "source": edge["source"],
        "target": edge["target"],
        "shared_skill_ids": shared_ids,
    })
    return edge


def _snapshot_shell(roles, nodes, input_sha256, edges):
    return {
        "status": "ready" if edges else "empty",
        "reason": None if edges else ("no_active_pathways" if not roles else
            "fewer_than_two_active_pathways" if len(roles) < 2 else "no_shared_verified_skills"),
        "algorithm_version": ALGORITHM_VERSION,
        "source_kind": SOURCE_KIND,
        "weight_definition": WEIGHT_DEFINITION,
        "input_sha256": input_sha256,
        "source_manifest": _source_manifest(roles),
        "source_count": len(roles),
        "node_count": len(nodes),
        "edge_count": len(edges),
        "nodes": nodes,
        "edges": edges,
    }


def build_relation_snapshot(pathways):
    """Build an immutable deterministic graph from already validated sources."""
    roles = sorted(pathways, key=lambda value: value["role_id"])
    ids = [role.get("role_id") for role in roles]
    if any(not isinstance(value, str) or not value for value in ids) or len(set(ids)) != len(ids):
        raise ValueError("Role identities must be non-empty and unique")
    nodes, skill_maps = _build_nodes(roles)
    input_sha256 = _source_fingerprint(roles)
    edges = [edge for left, right in combinations(roles, 2)
             if (edge := _make_edge(left, right, skill_maps, input_sha256)) is not None]
    return _snapshot_shell(roles, nodes, input_sha256, edges)


def build_incremental_relation_snapshot(pathways, previous_manifest, previous_edges):
    """Reuse unchanged-pair edges; recalculate only pairs touching changed roles."""
    roles = sorted(pathways, key=lambda value: value["role_id"])
    ids = [role.get("role_id") for role in roles]
    if any(not isinstance(value, str) or not value for value in ids) or len(set(ids)) != len(ids):
        raise ValueError("Role identities must be non-empty and unique")
    nodes, skill_maps = _build_nodes(roles)
    current_manifest = _source_manifest(roles)
    current_by_id = {row["role_id"]: row for row in current_manifest}
    previous_by_id = {row["role_id"]: row for row in previous_manifest}
    changed = {role_id for role_id in current_by_id.keys() | previous_by_id.keys()
               if current_by_id.get(role_id) != previous_by_id.get(role_id)}
    input_sha256 = _fingerprint_manifest(current_manifest)

    old_by_pair = {}
    for row in previous_edges:
        source, target = row.get("source_role_id"), row.get("target_role_id")
        pair = (source, target)
        if (not isinstance(source, str) or not isinstance(target, str) or source >= target
                or pair in old_by_pair):
            raise RuntimeError("Previous relation run has invalid or duplicate role pairs")
        old_by_pair[pair] = row

    edges = []
    reused_edges = 0
    recomputed_pairs = 0
    for left, right in combinations(roles, 2):
        pair = (left["role_id"], right["role_id"])
        old = old_by_pair.get(pair)
        unchanged_pair = pair[0] not in changed and pair[1] not in changed
        if unchanged_pair:
            if not old:
                continue
            edge = _make_edge(left, right, skill_maps, input_sha256)
            saved_ids = _json(old.get("shared_skill_ids_json"))
            saved_evidence = _json(old.get("evidence_json"))
            expected_evidence = {"shared_skills": edge["shared_skills"]} if edge else None
            if (edge is None or old.get("relation_type") != "shared_skill"
                    or old.get("source_revision") != edge["source_revision"]
                    or old.get("source_sha256") != edge["source_sha256"]
                    or old.get("target_revision") != edge["target_revision"]
                    or old.get("target_sha256") != edge["target_sha256"]
                    or int(old.get("shared_skill_count", -1)) != edge["shared_skill_count"]
                    or int(old.get("union_skill_count", -1)) != edge["union_skill_count"]
                    or abs(float(old.get("weight", -1)) - edge["weight"]) > 1e-8
                    or saved_ids != [skill["skill_id"] for skill in edge["shared_skills"]]
                    or saved_evidence != expected_evidence):
                raise RuntimeError("Previous unchanged relation differs from its current pathway evidence")
            reused_edges += 1
        else:
            recomputed_pairs += 1
            edge = _make_edge(left, right, skill_maps, input_sha256)
        if edge is not None:
            edges.append(edge)

    snapshot = _snapshot_shell(roles, nodes, input_sha256, edges)
    snapshot["incremental"] = {"reused_edges": reused_edges, "recomputed_pairs": recomputed_pairs}
    return snapshot


def _edge_parameters(edge, run_id):
    return {
        "id": edge["id"], "run_id": run_id,
        "source_role_id": edge["source"], "source_revision": edge["source_revision"],
        "source_sha256": edge["source_sha256"],
        "target_role_id": edge["target"], "target_revision": edge["target_revision"],
        "target_sha256": edge["target_sha256"], "relation_type": edge["relation_type"],
        "weight": edge["weight"], "shared_skill_count": edge["shared_skill_count"],
        "union_skill_count": edge["union_skill_count"],
        "shared_skill_ids_json": _canonical([skill["skill_id"] for skill in edge["shared_skills"]]),
        "evidence_json": _canonical({"shared_skills": edge["shared_skills"]}),
    }


@contextmanager
def _write_transaction(mysql):
    with mysql.engine.connect() as conn:
        is_mysql = conn.dialect.name in {"mysql", "mariadb"}
        if is_mysql:
            locked = conn.execute(text("SELECT GET_LOCK(:name,10)"), {"name": _LOCK_NAME}).scalar() == 1
            conn.commit()
            if not locked:
                raise RuntimeError("Career relation computation is already running")
        try:
            with conn.begin():
                yield conn
        finally:
            if is_mysql and not conn.invalidated:
                conn.execute(text("SELECT RELEASE_LOCK(:name)"), {"name": _LOCK_NAME})
                conn.commit()


def _same_persisted_run(conn, run_id, snapshot):
    row = conn.execute(text("SELECT algorithm_version,input_sha256,source_manifest_json,source_count,node_count,edge_count,created_at "
        "FROM career_relation_run WHERE run_id=:run_id"), {"run_id": run_id}).mappings().first()
    if not row:
        return None
    expected = (ALGORITHM_VERSION, snapshot["input_sha256"], snapshot["source_count"],
                snapshot["node_count"], snapshot["edge_count"])
    actual = (row["algorithm_version"], row["input_sha256"], int(row["source_count"]),
              int(row["node_count"]), int(row["edge_count"]))
    if (actual != expected or _json(row["source_manifest_json"]) != snapshot["source_manifest"]):
        raise RuntimeError("Existing relation run identity/counts do not match its deterministic key")
    saved = conn.execute(text("SELECT edge_id,source_role_id,source_revision,source_sha256,target_role_id,target_revision,target_sha256,"
        "relation_type,weight,shared_skill_count,union_skill_count,shared_skill_ids_json,evidence_json "
        "FROM career_role_relation WHERE run_id=:run_id ORDER BY edge_id"),
        {"run_id": run_id}).mappings().all()
    expected_edges = sorted((_edge_parameters(edge, run_id) for edge in snapshot["edges"]), key=lambda value: value["id"])
    if len(saved) != len(expected_edges):
        raise RuntimeError("Persisted relation edge count does not match its run")
    for saved_edge, expected_edge in zip(saved, expected_edges):
        if (saved_edge["edge_id"] != expected_edge["id"]
                or saved_edge["source_role_id"] != expected_edge["source_role_id"]
                or saved_edge["source_revision"] != expected_edge["source_revision"]
                or saved_edge["source_sha256"] != expected_edge["source_sha256"]
                or saved_edge["target_role_id"] != expected_edge["target_role_id"]
                or saved_edge["target_revision"] != expected_edge["target_revision"]
                or saved_edge["target_sha256"] != expected_edge["target_sha256"]
                or saved_edge["relation_type"] != expected_edge["relation_type"]
                or abs(float(saved_edge["weight"]) - expected_edge["weight"]) > 1e-8
                or int(saved_edge["shared_skill_count"]) != expected_edge["shared_skill_count"]
                or int(saved_edge["union_skill_count"]) != expected_edge["union_skill_count"]
                or _json(saved_edge["shared_skill_ids_json"]) != _json(expected_edge["shared_skill_ids_json"])
                or _json(saved_edge["evidence_json"]) != _json(expected_edge["evidence_json"])):
            raise RuntimeError("Persisted relation edge differs from its deterministic source evidence")
    return row


def _previous_run(conn):
    row = conn.execute(text("SELECT run_id,input_sha256,source_manifest_json,source_count,node_count,edge_count "
        "FROM career_relation_run WHERE algorithm_version=:algorithm_version "
        "ORDER BY created_at DESC,run_id DESC LIMIT 1"), {"algorithm_version": ALGORITHM_VERSION}).mappings().first()
    if not row:
        return None
    manifest = _json(row["source_manifest_json"])
    if (not isinstance(manifest, list) or _fingerprint_manifest(manifest) != row["input_sha256"]
            or row["run_id"] != row["input_sha256"]):
        raise RuntimeError("Previous relation run manifest does not match its immutable fingerprint")
    edges = conn.execute(text("SELECT source_role_id,target_role_id,source_revision,source_sha256,target_revision,target_sha256,"
        "relation_type,weight,shared_skill_count,union_skill_count,shared_skill_ids_json,evidence_json "
        "FROM career_role_relation WHERE run_id=:run_id ORDER BY source_role_id,target_role_id"),
        {"run_id": row["run_id"]}).mappings().all()
    if (len(edges) != int(row["edge_count"]) or len(manifest) != int(row["source_count"])
            or len(manifest) != int(row["node_count"])):
        raise RuntimeError("Previous relation run is incomplete; refusing incremental reuse")
    return {"run_id": row["run_id"], "manifest": manifest, "edges": [dict(edge) for edge in edges]}


def compute_and_persist(mysql, *, apply=False):
    """Calculate current graph; write an immutable, idempotent run only on apply."""
    pathways = load_active_pathways(mysql)
    if not apply:
        snapshot = build_relation_snapshot(pathways)
        result = {key: value for key, value in snapshot.items()
                  if key not in {"nodes", "edges", "source_manifest"}}
        result.update({"run_id": snapshot["input_sha256"], "writes_performed": False,
                       "incremental": {"mode": "dry_run_full_preview", "reused_edges": 0,
                           "recomputed_pairs": len(pathways) * (len(pathways) - 1) // 2}})
        result["status"] = "dry_run"
        result["sample_edges"] = [{
            "source": edge["source"], "target": edge["target"], "weight": edge["weight"],
            "shared_skill_count": edge["shared_skill_count"], "union_skill_count": edge["union_skill_count"],
        } for edge in snapshot["edges"][:20]]
        return result

    current_input_sha256 = _source_fingerprint(pathways)
    run_id = current_input_sha256
    with _write_transaction(mysql) as conn:
        existing_run = conn.execute(text("SELECT run_id FROM career_relation_run WHERE run_id=:run_id"),
            {"run_id": run_id}).first()
        if existing_run:
            snapshot = build_relation_snapshot(pathways)
            existing = _same_persisted_run(conn, run_id, snapshot)
            result = {key: value for key, value in snapshot.items()
                      if key not in {"nodes", "edges", "source_manifest"}}
            result.update({"run_id": run_id, "writes_performed": False,
                           "incremental": {"mode": "unchanged", "reused_edges": snapshot["edge_count"],
                               "recomputed_pairs": 0}})
            result.update({"status": "unchanged", "computed_at": str(existing["created_at"])})
            return result
        previous = _previous_run(conn)
        if previous:
            snapshot = build_incremental_relation_snapshot(pathways, previous["manifest"], previous["edges"])
            snapshot["incremental"].update({"mode": "incremental", "previous_run_id": previous["run_id"]})
        else:
            snapshot = build_relation_snapshot(pathways)
            snapshot["incremental"] = {"mode": "initial_full_build", "previous_run_id": None,
                "reused_edges": 0, "recomputed_pairs": len(pathways) * (len(pathways) - 1) // 2}
        result = {key: value for key, value in snapshot.items()
                  if key not in {"nodes", "edges", "source_manifest"}}
        result.update({"run_id": run_id, "writes_performed": False})
        conn.execute(text("INSERT INTO career_relation_run "
            "(run_id,algorithm_version,input_sha256,source_manifest_json,source_count,node_count,edge_count,created_at) "
            "VALUES (:run_id,:algorithm_version,:input_sha256,:source_manifest_json,:source_count,:node_count,:edge_count,CURRENT_TIMESTAMP)"), {
                "run_id": run_id, "algorithm_version": ALGORITHM_VERSION,
                "input_sha256": snapshot["input_sha256"], "source_count": snapshot["source_count"],
                "source_manifest_json": _canonical(snapshot["source_manifest"]),
                "node_count": snapshot["node_count"], "edge_count": snapshot["edge_count"],
            })
        if snapshot["edges"]:
            conn.execute(text("INSERT INTO career_role_relation "
                "(edge_id,run_id,source_role_id,source_revision,source_sha256,target_role_id,target_revision,target_sha256,"
                "relation_type,weight,shared_skill_count,union_skill_count,shared_skill_ids_json,evidence_json,created_at) "
                "VALUES (:id,:run_id,:source_role_id,:source_revision,:source_sha256,:target_role_id,:target_revision,:target_sha256,"
                ":relation_type,:weight,:shared_skill_count,:union_skill_count,:shared_skill_ids_json,:evidence_json,CURRENT_TIMESTAMP)"),
                [_edge_parameters(edge, run_id) for edge in snapshot["edges"]])
        count = conn.execute(text("SELECT COUNT(*) FROM career_role_relation WHERE run_id=:run_id"),
            {"run_id": run_id}).scalar_one()
        if int(count) != snapshot["edge_count"]:
            raise RuntimeError("Persisted relation count verification failed")
    result.update({"status": "applied", "writes_performed": True})
    return result


def _iso(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value) if value is not None else None


def read_current_graph(mysql):
    """Return only the stored projection matching the current active source digest."""
    pathways = load_active_pathways(mysql)
    roles = sorted(pathways, key=lambda value: value["role_id"])
    nodes, _ = _build_nodes(roles)
    input_sha256 = _source_fingerprint(roles)
    base = {
        "algorithm_version": ALGORITHM_VERSION,
        "source_kind": SOURCE_KIND,
        "weight_definition": WEIGHT_DEFINITION,
        "input_sha256": input_sha256,
        "source_count": len(roles),
        "node_count": len(nodes),
        "nodes": nodes,
    }
    if not pathways:
        base.update(status="empty", reason="no_active_pathways", edge_count=0, edges=[], computed_at=None)
        return base
    run = mysql.execute_single("SELECT run_id,algorithm_version,input_sha256,source_manifest_json,source_count,node_count,edge_count,created_at "
        "FROM career_relation_run WHERE run_id=:run_id AND algorithm_version=:algorithm_version",
        {"run_id": input_sha256, "algorithm_version": ALGORITHM_VERSION})
    if run is None:
        base.update(status="pending", reason="relationship_projection_not_computed", edge_count=0,
                    edges=[], computed_at=None)
        return base
    if (run.get("input_sha256") != input_sha256
            or int(run.get("source_count", -1)) != len(roles)
            or int(run.get("node_count", -1)) != len(nodes)
            or _json(run.get("source_manifest_json")) != _source_manifest(roles)):
        raise RuntimeError("Stored relation run is bound to a different active source set")
    rows = mysql.execute("SELECT edge_id,source_role_id,target_role_id,relation_type,weight,shared_skill_count,"
        "union_skill_count,shared_skill_ids_json,evidence_json,source_revision,source_sha256,target_revision,target_sha256 "
        "FROM career_role_relation WHERE run_id=:run_id ORDER BY source_role_id,target_role_id",
        {"run_id": run["run_id"]})
    if len(rows) != int(run["edge_count"]):
        raise RuntimeError("Stored relation edge count does not match its immutable run")
    node_by_id = {node["id"]: node for node in base["nodes"]}
    node_skills = {node["id"]: {skill["skill_id"]: skill for skill in node["skills"]}
                   for node in base["nodes"]}
    role_by_id = {role["role_id"]: role for role in roles}
    edges = []
    for row in rows:
        evidence = _json(row["evidence_json"])
        shared = evidence.get("shared_skills") if isinstance(evidence, dict) else None
        shared_ids = _json(row["shared_skill_ids_json"])
        source = node_by_id.get(row["source_role_id"])
        target = node_by_id.get(row["target_role_id"])
        source_skills = node_skills.get(row["source_role_id"], {})
        target_skills = node_skills.get(row["target_role_id"], {})
        expected_shared = sorted(source_skills.keys() & target_skills.keys())
        union_count = len(source_skills.keys() | target_skills.keys())
        expected_edge = (_make_edge(role_by_id[row["source_role_id"]], role_by_id[row["target_role_id"]],
                                    node_skills, input_sha256)
                         if row["source_role_id"] in role_by_id and row["target_role_id"] in role_by_id else None)
        evidence_matches = isinstance(shared, list) and [item.get("skill_id") for item in shared] == expected_shared
        if evidence_matches:
            for item in shared:
                left = source_skills[item["skill_id"]]
                right = target_skills[item["skill_id"]]
                if (item.get("name") != left["name"] or item.get("name") != right["name"]
                        or item.get("source_evidence") != left["evidence"]
                        or item.get("target_evidence") != right["evidence"]):
                    evidence_matches = False
                    break
        if (not source or not target or row["source_role_id"] >= row["target_role_id"]
                or expected_edge is None or row["edge_id"] != expected_edge["id"]
                or source["revision"] != row["source_revision"]
                or source["content_sha256"] != row["source_sha256"]
                or target["revision"] != row["target_revision"]
                or target["content_sha256"] != row["target_sha256"]
                or row["relation_type"] != "shared_skill"
                or not expected_shared
                or not isinstance(shared_ids, list) or shared_ids != expected_shared
                or not isinstance(shared, list) or len(shared) != int(row["shared_skill_count"])
                or int(row["shared_skill_count"]) != len(expected_shared)
                or int(row["union_skill_count"]) != union_count
                or (union_count and abs(float(row["weight"]) - round(len(expected_shared) / union_count, 8)) > 1e-8)
                or not evidence_matches):
            raise RuntimeError("Stored relation evidence is incomplete or stale")
        edges.append({
            "id": row["edge_id"], "source": row["source_role_id"], "target": row["target_role_id"],
            "relation_type": row["relation_type"], "weight": float(row["weight"]),
            "shared_skill_count": int(row["shared_skill_count"]), "union_skill_count": int(row["union_skill_count"]),
            "shared_skills": shared,
            "source_revision": row["source_revision"], "target_revision": row["target_revision"],
        })
    base.update(status="ready" if edges else "empty",
                reason=None if edges else ("fewer_than_two_active_pathways" if len(pathways) < 2
                    else "no_shared_verified_skills"),
                edge_count=len(edges), edges=edges, computed_at=_iso(run.get("created_at")))
    return base
