"""Owner-scoped, append-only review of confirmed resume entity-link proposals.

This is a short MySQL transaction, not an Agent execution graph. Model proposals
remain proposals: accepting a `related` link never changes its relation or writes
to the public ontology, personal graph, or confirmed resume.
"""

from __future__ import annotations

import hashlib
import json
import re

from sqlalchemy import text

from .pipeline import require_confirmed_profile
from .pipeline_events import ConfirmationTransaction


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{8,128}$")
_ACTIONS = frozenset({"accept", "reject", "defer"})


class ReviewConflict(ValueError):
    """Stale source, revision, or incompatible idempotency replay."""


class _LockedConfirmation(ConfirmationTransaction):
    def fetch_all(self, query, parameters=None):
        return self.execute(query + " FOR UPDATE", parameters)


def _json(value):
    return json.loads(value) if isinstance(value, str) else value


def _confirmed_source(conn, student_id: str):
    # Same lock order as resume confirmation: student_profile, then pipeline_task.
    row = conn.execute(text(
        "SELECT student_id FROM student_profile WHERE student_id=:sid FOR UPDATE"
    ), {"sid": student_id}).first()
    if row is None:
        raise LookupError("当前用户没有已确认简历")
    try:
        profile = require_confirmed_profile(_LockedConfirmation(conn), student_id)
    except ValueError as exc:
        raise ReviewConflict("当前简历未确认或确认来源不一致") from exc
    linking = profile.get("entity_linking")
    if not isinstance(linking, dict) or not _SHA256.fullmatch(str(linking.get("source_sha256") or "")):
        raise ReviewConflict("当前简历没有可审阅的实体提议，请重新执行实体抽取")
    mentions = linking.get("mentions")
    decisions = linking.get("decisions")
    candidates = linking.get("candidates")
    if not isinstance(mentions, list) or not isinstance(decisions, list) or not isinstance(candidates, list):
        raise ReviewConflict("实体提议结构未通过校验，请重新执行实体抽取")
    task_id = profile["_resume_confirmation"]["task_id"]
    return task_id, linking


def _latest(conn, student_id, task_id):
    return conn.execute(text(
        "SELECT revision, snapshot_json, request_id FROM resume_entity_review "
        "WHERE student_id=:sid AND source_confirmation_task_id=:tid "
        "ORDER BY revision DESC LIMIT 1"
    ), {"sid": student_id, "tid": task_id}).mappings().first()


def _result(task_id, linking, latest, *, replayed=False):
    snapshot = _json(latest["snapshot_json"]) if latest else []
    by_index = {item["mention_index"]: item for item in snapshot}
    proposals = {d.get("mention_index"): d for d in linking["decisions"] if isinstance(d, dict)}
    mentions = []
    for index, mention in enumerate(linking["mentions"]):
        proposal = proposals.get(index)
        candidate = None
        if isinstance(proposal, dict):
            candidate_index = proposal.get("candidate_index")
            if type(candidate_index) is int and 0 <= candidate_index < len(linking["candidates"]):
                candidate = linking["candidates"][candidate_index]
        mentions.append({"mention_index": index, "mention": mention,
                         "proposal": proposal, "candidate": candidate,
                         "review": by_index.get(index)})
    return {"source_confirmation_task_id": task_id,
            "source_sha256": linking["source_sha256"],
            "entity_linking_status": linking.get("status", "unresolved"),
            "entity_linking_reason": linking.get("reason"),
            "next_action": linking.get("next_action"),
            "revision": int(latest["revision"]) if latest else 0,
            "mentions": mentions, "reviews": snapshot,
            "replayed": replayed,
            "graph_publication_status": "not_requested"}


def read_entity_review(mysql, *, student_id: str) -> dict:
    with mysql.engine.begin() as conn:
        task_id, linking = _confirmed_source(conn, student_id)
        result = _result(task_id, linking, _latest(conn, student_id, task_id))
    if result["revision"]:
        try:
            from .personal_entity_link_jobs import projection_state
            state = projection_state(mysql, student_id=student_id, task_id=task_id,
                                     revision=result["revision"])
        except Exception:
            state = {"status": "not_available", "retryable": True,
                     "reason": "projection_storage_unavailable"}
        result["graph_projection"] = state
        result["graph_publication_status"] = state["status"] if state else "pending"
    return result


def _validated_payload(payload):
    if not isinstance(payload, dict) or set(payload) != {
        "source_confirmation_task_id", "source_sha256", "request_id", "expected_revision", "reviews"
    }:
        raise ValueError("审阅请求字段不完整或含不受支持字段")
    task_id, source_hash, request_id = (payload[k] for k in
        ("source_confirmation_task_id", "source_sha256", "request_id"))
    revision, reviews = payload["expected_revision"], payload["reviews"]
    if not isinstance(task_id, str) or not task_id.startswith("task_") or len(task_id) > 64:
        raise ValueError("source_confirmation_task_id 无效")
    if not isinstance(source_hash, str) or not _SHA256.fullmatch(source_hash):
        raise ValueError("source_sha256 无效")
    if not isinstance(request_id, str) or not _REQUEST_ID.fullmatch(request_id):
        raise ValueError("request_id 无效")
    if type(revision) is not int or revision < 0:
        raise ValueError("expected_revision 无效")
    if not isinstance(reviews, list) or not 1 <= len(reviews) <= 100:
        raise ValueError("reviews 必须为 1 至 100 项")
    seen = set()
    for item in reviews:
        if not isinstance(item, dict) or not {"mention_index", "action"} <= set(item) or set(item) - {
            "mention_index", "action", "note"
        }:
            raise ValueError("审阅项字段无效")
        index, action, note = item["mention_index"], item["action"], item.get("note", "")
        if type(index) is not int or index < 0 or index in seen:
            raise ValueError("mention_index 重复或无效")
        seen.add(index)
        if not isinstance(action, str) or action not in _ACTIONS:
            raise ValueError("action 仅支持 accept/reject/defer")
        if not isinstance(note, str) or len(note) > 500:
            raise ValueError("note 长度超过限制")
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _validate_against_proposals(linking, reviews):
    decisions = {d.get("mention_index"): d for d in linking["decisions"] if isinstance(d, dict)}
    for item in reviews:
        index = item["mention_index"]
        if index >= len(linking["mentions"]):
            raise ValueError("mention_index 超出当前简历实体范围")
        if item["action"] != "accept":
            continue
        proposal = decisions.get(index)
        candidate_index = proposal.get("candidate_index") if isinstance(proposal, dict) else None
        if (not isinstance(proposal, dict) or proposal.get("decision") not in {"equivalent", "related"}
                or type(candidate_index) is not int or not 0 <= candidate_index < len(linking["candidates"])):
            raise ValueError("该实体没有可接受的服务端对齐提议")


def write_entity_review(mysql, *, student_id: str, payload: dict) -> dict:
    payload_hash = _validated_payload(payload)
    with mysql.engine.begin() as conn:
        task_id, linking = _confirmed_source(conn, student_id)
        if (payload["source_confirmation_task_id"] != task_id
                or payload["source_sha256"] != linking["source_sha256"]):
            raise ReviewConflict("简历版本已变化，请刷新实体审阅")
        _validate_against_proposals(linking, payload["reviews"])
        previous = conn.execute(text(
            "SELECT source_confirmation_task_id, source_sha256, payload_sha256, revision, "
            "snapshot_json, request_id FROM resume_entity_review "
            "WHERE student_id=:sid AND request_id=:rid LIMIT 1"
        ), {"sid": student_id, "rid": payload["request_id"]}).mappings().first()
        if previous is not None:
            if (previous["payload_sha256"] != payload_hash
                    or previous["source_confirmation_task_id"] != task_id
                    or previous["source_sha256"] != linking["source_sha256"]):
                raise ReviewConflict("request_id 已用于不同的审阅请求")
            replay = _result(task_id, linking, previous, replayed=True)
        else:
            replay = None
            latest = _latest(conn, student_id, task_id)
            revision = int(latest["revision"]) if latest else 0
            if payload["expected_revision"] != revision:
                raise ReviewConflict("审阅版本已变化，请刷新后重试")
            snapshot = _json(latest["snapshot_json"]) if latest else []
            by_index = {item["mention_index"]: item for item in snapshot}
            proposals = {d.get("mention_index"): d for d in linking["decisions"] if isinstance(d, dict)}
            for item in payload["reviews"]:
                index = item["mention_index"]
                proposal = proposals.get(index)
                # Keep the original model relation. An accepted related proposal is
                # never silently promoted to canonical equivalence.
                by_index[index] = {"mention_index": index, "action": item["action"],
                                   "note": item.get("note", ""),
                                   "relation": proposal.get("decision") if item["action"] == "accept" else None,
                                   "candidate_index": proposal.get("candidate_index") if item["action"] == "accept" else None}
            snapshot = [by_index[index] for index in sorted(by_index)]
            conn.execute(text(
                "INSERT INTO resume_entity_review "
                "(student_id, source_confirmation_task_id, source_sha256, request_id, payload_sha256, "
                "revision, reviews_json, snapshot_json) "
                "VALUES (:sid, :tid, :sha, :rid, :ph, :rev, :reviews, :snapshot)"
            ), {"sid": student_id, "tid": task_id, "sha": linking["source_sha256"],
                 "rid": payload["request_id"], "ph": payload_hash, "rev": revision + 1,
                 "reviews": json.dumps(payload["reviews"], ensure_ascii=False),
                 "snapshot": json.dumps(snapshot, ensure_ascii=False)})
    if replay is not None:
        # Idempotent POST returns the original record. It does not dispatch an
        # extra graph job or pretend that a later revision is this request.
        return replay
    # Return only authoritative committed state, not an optimistic model or UI value.
    persisted = read_entity_review(mysql, student_id=student_id)
    if (persisted["source_confirmation_task_id"] != task_id
            or persisted["source_sha256"] != linking["source_sha256"]
            or persisted["revision"] < revision + 1):
        raise RuntimeError("审阅已提交但回读未通过校验")
    try:
        from .personal_entity_link_jobs import request_entity_link_projection
        state = request_entity_link_projection(mysql, student_id=student_id,
            task_id=task_id, revision=revision + 1)
    except Exception:
        state = {"status": "not_available", "retryable": True,
                 "reason": "projection_storage_unavailable"}
    persisted["graph_projection"] = state
    persisted["graph_publication_status"] = state["status"]
    return persisted
