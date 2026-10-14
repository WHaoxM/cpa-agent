"""Reconcile public skill vectors from MySQL taxonomy without enriching facts."""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from typing import Any


COLLECTION = "skill_vectors"
SOURCE = "mysql.skill_taxonomy"
FORMAT = "taxonomy-structure-v1"
READ_SQL = """SELECT id, skill_name, category, sub_category, tier, dimension
              FROM skill_taxonomy ORDER BY id"""


def _canonical(row: dict[str, Any], model: str) -> dict[str, Any]:
    taxonomy_id = int(row["id"])
    name = str(row["skill_name"] or "").strip()
    if taxonomy_id <= 0 or not name:
        raise ValueError("skill_taxonomy contains an invalid id or blank skill_name")
    category = str(row.get("category") or "").strip()
    sub_category = str(row.get("sub_category") or "").strip()
    dimension = str(row.get("dimension") or "").strip()
    tier = int(row["tier"])
    parts = [f"技能：{name}"]
    if category:
        parts.append(f"类别：{category}")
    if sub_category:
        parts.append(f"子类：{sub_category}")
    text = "；".join(parts)
    identity = {
        "source": SOURCE, "source_format": FORMAT,
        "taxonomy_id": taxonomy_id, "skill_name": name,
        "category": category, "sub_category": sub_category,
        "dimension": dimension, "tier": tier, "embedding_model": model,
    }
    digest = hashlib.sha256(
        json.dumps({"text": text, **identity}, sort_keys=True,
                   ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {"text": text, "metadata": {**identity, "content_sha256": digest}}


def load_taxonomy(mysql: Any, model: str) -> list[dict[str, Any]]:
    """A successful empty read is not enough evidence to delete an index."""
    rows = mysql.execute(READ_SQL)
    if not rows:
        raise ValueError("skill_taxonomy is empty; refusing to reconcile")
    records = [_canonical(row, model) for row in rows]
    ids = [item["metadata"]["taxonomy_id"] for item in records]
    if len(ids) != len(set(ids)):
        raise ValueError("skill_taxonomy contains duplicate ids")
    return records


def _read_index(milvus: Any) -> tuple[dict[int, list[dict[str, Any]]], int]:
    if not milvus.connect():
        raise RuntimeError("Milvus connection failed")
    if not milvus._client.has_collection(COLLECTION):
        raise RuntimeError("skill_vectors collection is unavailable")
    # Persisted Milvus Lite collections can reopen in the released state.
    milvus._client.load_collection(COLLECTION)
    managed: dict[int, list[dict[str, Any]]] = defaultdict(list)
    unmanaged = 0
    offset = 0
    while True:
        batch = milvus._client.query(
            collection_name=COLLECTION, filter="id >= 0",
            output_fields=["id", "text", "metadata"], limit=500, offset=offset,
        )
        for row in batch:
            try:
                meta = json.loads(row.get("metadata") or "{}")
            except (TypeError, ValueError):
                meta = {}
            if meta.get("source") != SOURCE:
                unmanaged += 1
                continue
            if meta.get("source_format") != FORMAT or not isinstance(meta.get("taxonomy_id"), int):
                raise ValueError("ambiguous managed skill vector metadata; refusing to mutate")
            managed[meta["taxonomy_id"]].append({"id": row["id"], "text": row.get("text"), "metadata": meta})
        offset += len(batch)
        if len(batch) < 500:
            break
    return managed, unmanaged


def reconcile_skill_vectors(
    mysql: Any, milvus: Any, embedding: Any, *, allow_unmanaged: bool = False,
    expected_source_count: int | None = None,
) -> dict[str, int]:
    """Insert missing/changed rows first, then remove obsolete/duplicate rows.

    Interrupted operations are repaired on replay. Untagged rows are never
    silently adopted or removed; the default is to fail closed.
    """
    desired = load_taxonomy(mysql, str(embedding.model_name))
    if expected_source_count is not None and len(desired) != expected_source_count:
        raise ValueError("skill_taxonomy count differs from expected_source_count")
    existing, unmanaged = _read_index(milvus)
    if unmanaged and not allow_unmanaged:
        raise ValueError(f"skill_vectors contains {unmanaged} unmanaged rows; refusing to mutate")
    desired_by_id = {item["metadata"]["taxonomy_id"]: item for item in desired}
    to_insert = [
        item for taxonomy_id, item in desired_by_id.items()
        if not any(row["text"] == item["text"] and row["metadata"] == item["metadata"]
                   for row in existing.get(taxonomy_id, []))
    ]
    if to_insert:
        vectors = embedding.encode([item["text"] for item in to_insert], batch_size=16)
        if len(vectors) != len(to_insert) or any(len(vector) != 1024 for vector in vectors):
            raise ValueError("embedding result count/dimension mismatch; refusing to write")
        milvus.insert(COLLECTION, [item["text"] for item in to_insert], vectors,
                      [item["metadata"] for item in to_insert])

    # Re-read after insert. Never delete old rows unless every target is visible.
    current, _ = _read_index(milvus)
    delete_ids = []
    missing = []
    for taxonomy_id, target in desired_by_id.items():
        if not any(row["text"] == target["text"] and row["metadata"] == target["metadata"]
                   for row in current.get(taxonomy_id, [])):
            missing.append(taxonomy_id)
    if missing:
        raise RuntimeError(f"new vectors not visible for taxonomy ids {missing}")
    for taxonomy_id, rows in current.items():
        target = desired_by_id.get(taxonomy_id)
        keeper_found = False
        for row in rows:
            matches = target is not None and row["text"] == target["text"] and row["metadata"] == target["metadata"]
            if matches and not keeper_found:
                keeper_found = True
            else:
                delete_ids.append(row["id"])
    if delete_ids:
        milvus._client.delete(collection_name=COLLECTION, ids=delete_ids)
    verified, remaining_unmanaged = _read_index(milvus)
    if set(verified) != set(desired_by_id) or any(
        len(rows) != 1 or rows[0]["text"] != desired_by_id[taxonomy_id]["text"]
        or rows[0]["metadata"] != desired_by_id[taxonomy_id]["metadata"]
        for taxonomy_id, rows in verified.items()
    ):
        raise RuntimeError("skill index verification failed; replay is safe")
    return {"source_rows": len(desired), "inserted": len(to_insert),
            "deleted": len(delete_ids), "verified": len(verified),
            "unmanaged": remaining_unmanaged}


def project_public_skill_vector_hits(mysql: Any, hits: list[dict], model: str):
    """Never expose unbound index text as an authoritative public skill."""
    from .job_publication import canonical_json

    desired = {item["metadata"]["taxonomy_id"]: item
               for item in load_taxonomy(mysql, model)}
    if not hits:
        # A non-empty taxonomy with a zero-hit, unfiltered ANN query means
        # the serving index is empty or broken, not that no skill exists.
        raise RuntimeError("skill_vectors is empty while skill_taxonomy is populated")
    valid = []
    rejected = 0
    for hit in hits:
        try:
            raw = hit["metadata"]
            metadata = json.loads(raw) if isinstance(raw, str) else raw
            taxonomy_id = metadata.get("taxonomy_id")
            expected = desired.get(taxonomy_id) if type(taxonomy_id) is int else None
            distance = hit.get("distance")
            if (expected is None or metadata != expected["metadata"]
                    or hit.get("text") != expected["text"]
                    or type(distance) not in (float, int) or not math.isfinite(distance)):
                rejected += 1
                continue
            valid.append({"id": hit["id"], "distance": distance,
                          "text": expected["text"],
                          "metadata": canonical_json(expected["metadata"])})
        except (TypeError, ValueError, KeyError, AttributeError):
            rejected += 1
    return valid, rejected
