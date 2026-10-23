"""Version-bound public pathway derivations; SQL remains the only fact owner.

Reuse: personal_graph.py's Neo4j execute_write + in-transaction verification;
skill_vector_index.py's source-bound reconcile/read-back (without old-row deletion);
neo4j driver and PyMilvus fixed-schema/upsert/filter APIs. Inspected local OSS
references: OpenCode 388406238b packages/core/src/v1/permission.ts and Codex
a06545b311 codex-rs/core/src/tools/registry.rs; neither supplies a business graph
publisher. No new Agent runtime, parser, generic index framework or vector client
is introduced. Structured SQL facts are the chunks; oversized facts fail explicitly
instead of inventing a splitter or silently truncating them.

Callers must load the CURRENT SQL snapshot before every publish/read/search, and
recheck it before returning a response. No graph CURRENT pointer can authorize a
revision. This module never clears old graph nodes or any legacy vector collection.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from functools import lru_cache
from pathlib import Path
import re
from urllib.parse import urlsplit

SCHEMA_VERSION = "career-pathway/v1"
SOURCE_ID = "mysql.career_pathway_revision"
VECTOR_COLLECTION = "career_pathway_vectors_v1"
MODEL_NAME = "BAAI/bge-m3"
MODEL_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
VECTOR_DIMENSION = 1024
BUSINESS_FIELDS = ("schema_version", "role_id", "role_name", "revision", "jobs",
                   "salary_references", "skills", "courses", "prerequisites")
READ_FIELDS = {"content_sha256", "projection_status", "published_at"}
CHUNK_FORMAT = "career-pathway-fact-v1"
MAX_FACT_CHARS = 2000


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def canonical_payload_digest(payload):
    """Digest business snapshot, excluding only documented read-side additions."""
    if not isinstance(payload, dict) or set(payload) - set(BUSINESS_FIELDS) - READ_FIELDS:
        raise ValueError("unknown pathway snapshot fields")
    if set(BUSINESS_FIELDS) - set(payload):
        raise ValueError("incomplete pathway business snapshot")
    return hashlib.sha256(_json({k: payload[k] for k in BUSINESS_FIELDS}).encode()).hexdigest()


def _text(value, *, limit=2048):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError("invalid bounded text")
    if any(ord(c) < 32 and c not in "\n\t" for c in value):
        raise ValueError("control character in text")
    return value


def _url(value):
    parsed = urlsplit(_text(value))
    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("public source must be an absolute HTTP(S) URL without credentials")


def _integer(value):
    if type(value) is not int or value <= 0:
        raise ValueError("expected positive integer business identity")


def _evidence(value):
    if not isinstance(value, dict) or set(value) != {"url", "note"}:
        raise ValueError("source evidence needs url and note")
    _url(value["url"])
    _text(value["note"], limit=4000)


def _validated(payload):
    digest = canonical_payload_digest(payload)
    if payload.get("content_sha256", digest) != digest:
        raise ValueError("SQL snapshot digest mismatch")
    if payload["schema_version"] != SCHEMA_VERSION:
        raise ValueError("unsupported pathway schema")
    if not isinstance(payload["role_id"], str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", payload["role_id"]):
        raise ValueError("invalid role_id")
    _text(payload["role_name"], limit=200)
    if not isinstance(payload["revision"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", payload["revision"]):
        raise ValueError("invalid string revision identity")
    for key in ("skills", "courses", "prerequisites", "jobs", "salary_references"):
        if not isinstance(payload[key], list) or len(payload[key]) > 500:
            raise ValueError("pathway arrays must have at most 500 facts")
    if not payload["skills"] or not payload["courses"]:
        raise ValueError("pathway must have skills and courses")
    skill_ids = set()
    for row in payload["skills"]:
        if not isinstance(row, dict):
            raise ValueError("invalid skill fact")
        _integer(row.get("skill_id"))
        if row["skill_id"] in skill_ids:
            raise ValueError("duplicate skill_id")
        skill_ids.add(row["skill_id"])
        _text(row.get("name"), limit=200)
        if row.get("tier") not in {"foundation", "application", "engineering"}:
            raise ValueError("unknown reviewed skill tier")
        if row.get("requirement_kind") != "recommended":
            raise ValueError("pathway skills are recommendations, not scoring requirements")
        _evidence(row.get("evidence"))
    course_ids = set()
    for row in payload["courses"]:
        if not isinstance(row, dict):
            raise ValueError("invalid course fact")
        _text(row.get("course_id"), limit=64)
        _text(row.get("name"), limit=200)
        _url(row.get("source_url"))
        if row["course_id"] in course_ids:
            raise ValueError("duplicate course_id")
        course_ids.add(row["course_id"])
        for key in ("skill_ids", "chapter_ids"):
            values = row.get(key)
            if not isinstance(values, list) or not values or len(values) > 500:
                raise ValueError("course needs bounded canonical skill and chapter references")
            for value in values:
                _integer(value)
            if len(values) != len(set(values)):
                raise ValueError("duplicate course reference")
        if not set(row["skill_ids"]) <= skill_ids:
            raise ValueError("course references a missing skill")
    edges = set()
    predecessors = {i: set() for i in skill_ids}
    for row in payload["prerequisites"]:
        if not isinstance(row, dict):
            raise ValueError("invalid prerequisite fact")
        a, b = row.get("from_skill_id"), row.get("to_skill_id")
        _integer(a); _integer(b)
        if a not in skill_ids or b not in skill_ids or a == b or (a, b) in edges:
            raise ValueError("invalid or duplicate prerequisite reference")
        edges.add((a, b)); predecessors[b].add(a)
        _evidence(row.get("evidence"))
    from graphlib import TopologicalSorter, CycleError
    try:
        tuple(TopologicalSorter(predecessors).static_order())
    except CycleError as exc:
        raise ValueError("prerequisite cycle") from exc
    fact_ids = set()
    for row in payload["jobs"]:
        if not isinstance(row, dict):
            raise ValueError("invalid reference-only job fact")
        _text(row.get("fact_id"), limit=128)
        if row["fact_id"] in fact_ids:
            raise ValueError("duplicate job fact_id")
        fact_ids.add(row["fact_id"])
        for field in ("title", "employer", "source_fetched_at", "summary"):
            _text(row.get(field), limit=4000)
        _url(row.get("source_uri"))
        if row.get("usage") != "reference_only" or row.get("scoring_ready") is not False:
            raise ValueError("job facts must remain reference-only and non-scoring")
        if not isinstance(row.get("locations"), list) or not isinstance(row.get("requirements"), list):
            raise ValueError("invalid job locations or requirements")
    clean = json.loads(_json({k: payload[k] for k in BUSINESS_FIELDS}))
    return clean, {"source_id": SOURCE_ID, "role_id": clean["role_id"],
                   "revision": clean["revision"], "content_sha256": digest}


GRAPH_CONSTRAINTS = (
    "CREATE CONSTRAINT career_pathway_role_identity IF NOT EXISTS FOR (n:CareerPathwayRole) REQUIRE (n.source_id,n.role_id) IS UNIQUE",
    "CREATE CONSTRAINT career_pathway_revision_identity IF NOT EXISTS FOR (n:CareerPathwayRevision) REQUIRE (n.source_id,n.role_id,n.revision) IS UNIQUE",
    "CREATE CONSTRAINT career_pathway_skill_identity IF NOT EXISTS FOR (n:CareerPathwaySkill) REQUIRE (n.source_id,n.role_id,n.revision,n.skill_id) IS UNIQUE",
    "CREATE CONSTRAINT career_pathway_course_identity IF NOT EXISTS FOR (n:CareerPathwayCourse) REQUIRE (n.source_id,n.role_id,n.revision,n.course_id) IS UNIQUE",
)


def ensure_graph_schema(neo4j):
    """Explicit setup, not called inside projection or an HTTP read transaction."""
    for statement in GRAPH_CONSTRAINTS:
        neo4j.execute_query(statement, {}, read_only=False)
    return {"status": "ready", "constraints": len(GRAPH_CONSTRAINTS)}


def _graph_rows(clean, binding):
    base = {**binding, "visibility": "public"}
    skills = [{**base, "skill_id": row["skill_id"], "name": row["name"],
               "tier": row["tier"], "requirement_kind": row["requirement_kind"],
               "evidence_json": _json(row["evidence"])} for row in clean["skills"]]
    courses = [{**base, **{k: row[k] for k in ("course_id", "name", "skill_ids", "chapter_ids", "source_url")}}
               for row in clean["courses"]]
    prerequisites = [{**base, "from_skill_id": row["from_skill_id"],
                      "to_skill_id": row["to_skill_id"], "evidence_json": _json(row["evidence"]),
                      "semantics": "prerequisite_to_dependent"} for row in clean["prerequisites"]]
    covers = [{**base, "course_id": row["course_id"], "skill_id": sid}
              for row in clean["courses"] for sid in row["skill_ids"]]
    return {"skills": skills, "courses": courses, "prerequisites": prerequisites, "covers": covers}


GRAPH_READ = """
MATCH (r:CareerPathwayRevision {source_id:$source_id,role_id:$role_id,revision:$revision,content_sha256:$content_sha256})
WHERE labels(r)=['CareerPathwayRevision'] AND r.visibility='public' AND r.state='verified'
RETURN r.payload_json AS payload_json,
 [(r)-[:PATHWAY_HAS_SKILL]->(s:CareerPathwaySkill) WHERE labels(s)=['CareerPathwaySkill'] | properties(s)] AS skills,
 [(r)-[:PATHWAY_HAS_COURSE]->(c:CareerPathwayCourse) WHERE labels(c)=['CareerPathwayCourse'] | properties(c)] AS courses,
 [(r)-[:PATHWAY_HAS_SKILL]->(s:CareerPathwaySkill)-[e:PATHWAY_PREREQUISITE]->(t:CareerPathwaySkill)<-[:PATHWAY_HAS_SKILL]-(r)
  WHERE labels(s)=['CareerPathwaySkill'] AND labels(t)=['CareerPathwaySkill'] | properties(e)] AS prerequisites,
 [(r)-[:PATHWAY_HAS_COURSE]->(c:CareerPathwayCourse)-[e:PATHWAY_COVERS]->(s:CareerPathwaySkill)<-[:PATHWAY_HAS_SKILL]-(r)
  WHERE labels(c)=['CareerPathwayCourse'] AND labels(s)=['CareerPathwaySkill'] | properties(e)] AS covers
LIMIT 2
"""


def _graph_verified(rows, clean, binding):
    if len(rows) != 1 or rows[0].get("payload_json") != _json(clean):
        return False
    expected = _graph_rows(clean, binding)
    return all(sorted(_json(x) for x in rows[0].get(key, [])) == sorted(_json(x) for x in values)
               for key, values in expected.items())


def project_graph(neo4j, payload):
    clean, binding = _validated(payload)
    rows = _graph_rows(clean, binding)
    params = {**binding, **rows, "payload_json": _json(clean)}
    stage = "connect_graph"
    def write(tx):
        nonlocal stage
        stage = "merge_immutable_revision"
        initial = tx.run("""
            MERGE (p:CareerPathwayRole {source_id:$source_id,role_id:$role_id})
            ON CREATE SET p.visibility='public'
            MERGE (r:CareerPathwayRevision {source_id:$source_id,role_id:$role_id,revision:$revision})
            ON CREATE SET r.content_sha256=$content_sha256,r.state='building',r.visibility='public'
            MERGE (p)-[:PATHWAY_HAS_REVISION]->(r)
            RETURN r.content_sha256 AS content_sha256
        """, params).single()
        if not initial or initial["content_sha256"] != binding["content_sha256"]:
            raise ValueError("immutable graph revision collision")
        stage = "project_skill_nodes"
        tx.run("""
            MATCH (r:CareerPathwayRevision {source_id:$source_id,role_id:$role_id,revision:$revision})
            SET r.payload_json=$payload_json,r.visibility='public'
            WITH r UNWIND $skills AS row
            MERGE (s:CareerPathwaySkill {source_id:$source_id,role_id:$role_id,revision:$revision,skill_id:row.skill_id})
            SET s = row MERGE (r)-[:PATHWAY_HAS_SKILL]->(s)
        """, params).consume()
        stage = "project_course_nodes"
        tx.run("""
            MATCH (r:CareerPathwayRevision {source_id:$source_id,role_id:$role_id,revision:$revision})
            WITH r UNWIND $courses AS row
            MERGE (c:CareerPathwayCourse {source_id:$source_id,role_id:$role_id,revision:$revision,course_id:row.course_id})
            SET c = row MERGE (r)-[:PATHWAY_HAS_COURSE]->(c)
        """, params).consume()
        stage = "project_prerequisite_edges"
        tx.run("""
            UNWIND $prerequisites AS row
            MATCH (s:CareerPathwaySkill {source_id:$source_id,role_id:$role_id,revision:$revision,skill_id:row.from_skill_id})
            MATCH (t:CareerPathwaySkill {source_id:$source_id,role_id:$role_id,revision:$revision,skill_id:row.to_skill_id})
            MERGE (s)-[e:PATHWAY_PREREQUISITE]->(t) SET e = row
        """, params).consume()
        stage = "project_course_coverage"
        tx.run("""
            UNWIND $covers AS row
            MATCH (c:CareerPathwayCourse {source_id:$source_id,role_id:$role_id,revision:$revision,course_id:row.course_id})
            MATCH (s:CareerPathwaySkill {source_id:$source_id,role_id:$role_id,revision:$revision,skill_id:row.skill_id})
            MERGE (c)-[e:PATHWAY_COVERS]->(s) SET e = row
        """, params).consume()
        stage = "verify_graph_transaction"
        tx.run("""
            MATCH (r:CareerPathwayRevision {source_id:$source_id,role_id:$role_id,revision:$revision,content_sha256:$content_sha256})
            SET r.state='verified'
        """, params).consume()
        if not _graph_verified([dict(r) for r in tx.run(GRAPH_READ, binding)], clean, binding):
            raise ValueError("graph projection verification failed; transaction rolled back")
    try:
        with neo4j.session() as session:
            session.execute_write(write)
    except Exception as exc:
        return {"status": "partial", **binding, "reason": type(exc).__name__,
                "stage": stage,
                "next_action": "replay_current_sql_graph_projection"}
    return {"status": "ready", **binding,
            "skill_count": len(rows["skills"]), "course_count": len(rows["courses"]),
            "prerequisite_count": len(rows["prerequisites"]), "coverage_count": len(rows["covers"])}


def read_graph(neo4j, payload):
    clean, binding = _validated(payload)
    try:
        rows = neo4j.execute_query(GRAPH_READ, binding, read_only=True)
        if not _graph_verified(rows, clean, binding):
            raise ValueError("current SQL revision graph is incomplete or unverified")
    except Exception as exc:
        return {"status": "unavailable", **binding, "reason": type(exc).__name__,
                "next_action": "replay_current_sql_graph_projection"}
    return {"status": "ready", **binding, "skills": clean["skills"], "courses": clean["courses"],
            "prerequisites": clean["prerequisites"], "relation_semantics": "prerequisite_to_dependent"}


def _local_snapshot():
    roots = []
    if os.environ.get("SENTENCE_TRANSFORMERS_HOME"):
        roots.append(Path(os.environ["SENTENCE_TRANSFORMERS_HOME"]))
    if os.environ.get("HF_HOME"):
        roots.append(Path(os.environ["HF_HOME"]) / "hub")
    roots.append(Path(__file__).resolve().parents[3] / "models" / "embeddings")
    for root in roots:
        snapshot = root / "models--BAAI--bge-m3" / "snapshots" / MODEL_REVISION
        if snapshot.is_dir() and (snapshot / "modules.json").is_file():
            return snapshot.resolve()
    raise FileNotFoundError("pinned offline embedding snapshot is unavailable")


@lru_cache(maxsize=1)
def _pinned_embedding():
    """Thin revision adapter reuses EmbeddingService.encode, not its shared singleton.

    The existing singleton loader lacks revision/local_files_only parameters. An
    isolated instance avoids changing a live Agent's shared model configuration.
    """
    from sentence_transformers import SentenceTransformer, models
    from .embedding_service import EmbeddingService
    snapshot = _local_snapshot()
    # The installed sentence-transformers 2.x does not accept local_files_only
    # on SentenceTransformer itself. Reuse its supported module constructors,
    # forwarding offline flags to transformers' AutoConfig/Model/Tokenizer.
    # This is the exact reviewed BGE-M3 architecture, not dynamic module imports.
    manifest = json.loads((snapshot / "modules.json").read_text(encoding="utf-8"))
    expected = [("", "sentence_transformers.models.Transformer"),
                ("1_Pooling", "sentence_transformers.models.Pooling"),
                ("2_Normalize", "sentence_transformers.models.Normalize")]
    if [(row.get("path"), row.get("type")) for row in manifest] != expected:
        raise ValueError("pinned embedding module architecture drift")
    config = json.loads((snapshot / "sentence_bert_config.json").read_text(encoding="utf-8"))
    if set(config) != {"max_seq_length", "do_lower_case"} or config["max_seq_length"] != 8192 or config["do_lower_case"] is not False:
        raise ValueError("pinned embedding tokenizer configuration drift")
    offline = {"local_files_only": True, "trust_remote_code": False}
    transformer = models.Transformer(str(snapshot), **config,
                                     model_args=offline.copy(), tokenizer_args=offline.copy())
    modules = [transformer, models.Pooling.load(str(snapshot / "1_Pooling")), models.Normalize()]
    service = object.__new__(EmbeddingService)
    service._initialized = False
    EmbeddingService.__init__(service)
    service._model = SentenceTransformer(modules=modules)
    service.model_name = MODEL_NAME
    service.local_snapshot_path = str(snapshot)
    service.embedding_revision = snapshot.name
    return service


def _embedding_identity(embedding, requested):
    revision = MODEL_REVISION
    if embedding is not None:
        declared = getattr(embedding, "embedding_revision", None)
        path = getattr(embedding, "local_snapshot_path", None)
        if path:
            discovered = Path(path).name
            if declared and declared != discovered:
                raise ValueError("embedding snapshot path/revision mismatch")
            declared = discovered
        if getattr(embedding, "model_name", None) != MODEL_NAME or declared != MODEL_REVISION:
            raise ValueError("embedding must identify the pinned local BGE-M3 snapshot")
        revision = declared
    if requested is not None and requested != revision:
        raise ValueError("requested embedding revision mismatch")
    return {"embedding_model": MODEL_NAME, "embedding_revision": revision}


def _chunks(clean):
    facts = []
    for s in clean["skills"]:
        facts.append(("skill", str(s["skill_id"]), f"技能：{s['name']}；层级：{s['tier']}；建议依据：{s['evidence']['note']}；来源：{s['evidence']['url']}"))
    for c in clean["courses"]:
        facts.append(("course", c["course_id"], f"学习资料：{c['name']}；关联技能ID：{_json(c['skill_ids'])}；章节ID：{_json(c['chapter_ids'])}；来源：{c['source_url']}"))
    for edge in clean["prerequisites"]:
        key = f"{edge['from_skill_id']}:{edge['to_skill_id']}"
        facts.append(("prerequisite", key, f"建议先学习技能ID {edge['from_skill_id']}，再学习技能ID {edge['to_skill_id']}。依据：{edge['evidence']['note']}；来源：{edge['evidence']['url']}"))
    for j in clean["jobs"]:
        facts.append(("job_reference", j["fact_id"], f"参考岗位：{j['title']}；机构：{j['employer']}；地点：{_json(j['locations'])}；摘要：{j['summary']}；要求：{_json(j['requirements'])}；来源：{j['source_uri']}；仅供参考，不参与评分。"))
    if len(facts) > 500:
        raise ValueError("too many structured facts; publish a bounded pathway revision")
    for _, _, text in facts:
        if len(text) > MAX_FACT_CHARS:
            raise ValueError("oversized fact; split authoritative source facts in a new revision; nothing truncated")
    return facts


def _records(clean, binding, identity):
    result = []
    for kind, key, text in _chunks(clean):
        chunk_id = f"{CHUNK_FORMAT}:{kind}:{key}"
        chunk_hash = hashlib.sha256(text.encode()).hexdigest()
        values = {**binding, **identity, "visibility": "public", "kind": kind, "entity_key": key,
                  "chunk_id": chunk_id, "chunk_sha256": chunk_hash, "text": text}
        values["id"] = hashlib.sha256(_json(values).encode()).hexdigest()
        result.append(values)
    return result


_VARCHAR_FIELDS = {"id": 64, "source_id": 64, "role_id": 64, "content_sha256": 64, "visibility": 16,
                   "revision": 64,
                   "embedding_model": 128, "embedding_revision": 64, "kind": 32,
                   "entity_key": 256, "chunk_id": 512, "chunk_sha256": 64, "text": 8192}
VECTOR_OUTPUT_FIELDS = tuple(_VARCHAR_FIELDS)


def _raw_milvus(milvus):
    if hasattr(milvus, "_client"):
        if milvus._client is None and not milvus.connect():
            raise RuntimeError("Milvus unavailable")
        return milvus._client
    return milvus


def _ensure_vector_schema(raw, *, create):
    from pymilvus import DataType
    if not raw.has_collection(VECTOR_COLLECTION):
        if not create:
            raise RuntimeError("current pathway vector collection not built")
        schema = raw.create_schema(auto_id=False, enable_dynamic_field=False)
        for name, size in _VARCHAR_FIELDS.items():
            schema.add_field(field_name=name, datatype=DataType.VARCHAR,
                             max_length=size, is_primary=(name == "id"))
        schema.add_field(field_name="embedding", datatype=DataType.FLOAT_VECTOR, dim=VECTOR_DIMENSION)
        indexes = raw.prepare_index_params()
        indexes.add_index(field_name="embedding", index_type="AUTOINDEX", metric_type="COSINE")
        raw.create_collection(collection_name=VECTOR_COLLECTION, schema=schema, index_params=indexes)
    desc = raw.describe_collection(VECTOR_COLLECTION)
    fields = {f["name"]: f for f in desc.get("fields", [])}
    if (desc.get("auto_id") is not False or desc.get("enable_dynamic_field") is not False
            or set(fields) != set(VECTOR_OUTPUT_FIELDS) | {"embedding"}
            or not fields["id"].get("is_primary")
            or int(fields["embedding"].get("params", {}).get("dim", 0)) != VECTOR_DIMENSION
            or any(int(fields[n]["type"]) != int(DataType.VARCHAR)
                   or int(fields[n].get("params", {}).get("max_length", 0)) < size
                   for n, size in _VARCHAR_FIELDS.items())
            or int(fields["embedding"]["type"]) != int(DataType.FLOAT_VECTOR)):
        raise ValueError("existing pathway vector schema mismatch; explicit migration required")
    raw.load_collection(VECTOR_COLLECTION)


def _filter(binding, identity):
    return " and ".join(f"{key} == {_json(value)}" for key, value in {**binding, **identity, "visibility": "public"}.items())


def _encode(embedding, texts):
    model = getattr(embedding, "_model", None)
    tokenizer = getattr(model, "tokenizer", None)
    maximum = getattr(model, "max_seq_length", None)
    if tokenizer is not None and maximum:
        encoded = tokenizer(texts, add_special_tokens=True, truncation=False)
        if any(len(ids) > maximum for ids in encoded["input_ids"]):
            raise ValueError("fact exceeds model token limit; source split required, not truncation")
    vectors = embedding.encode(texts, normalize=True, batch_size=16)
    if len(vectors) != len(texts):
        raise ValueError("embedding result count mismatch")
    for vector in vectors:
        if (len(vector) != VECTOR_DIMENSION or any(type(v) not in (int, float) or not math.isfinite(v) for v in vector)
                or not any(v != 0 for v in vector)):
            raise ValueError("invalid 1024-dimensional embedding; no fake fallback")
    return vectors


def _index_rows(raw, binding, identity, limit):
    return raw.query(collection_name=VECTOR_COLLECTION, filter=_filter(binding, identity),
                     output_fields=list(VECTOR_OUTPUT_FIELDS), limit=limit, consistency_level="Strong")


def project_vectors(milvus, payload, embedding=None, embedding_revision=None):
    clean, binding = _validated(payload)
    stage = "model_identity"
    try:
        identity = _embedding_identity(embedding, embedding_revision)
        stage = "bounded_source_facts"
        records = _records(clean, binding, identity)
        stage = "connect_vector_store"
        raw = _raw_milvus(milvus)
        stage = "vector_schema"
        _ensure_vector_schema(raw, create=True)
        stage = "vector_supply_read"
        existing = {r["id"]: r for r in _index_rows(raw, binding, identity, 1000)}
        needed = [r for r in records if existing.get(r["id"]) != r]
        if needed:
            stage = "load_pinned_offline_model"
            embedding = embedding or _pinned_embedding()
            if _embedding_identity(embedding, embedding_revision) != identity:
                raise ValueError("embedding identity changed during projection")
            stage = "encode_bounded_facts"
            vectors = _encode(embedding, [r["text"] for r in needed])
            stage = "upsert_vector_facts"
            raw.upsert(collection_name=VECTOR_COLLECTION,
                       data=[{**r, "embedding": v} for r, v in zip(needed, vectors)])
            raw.flush(VECTOR_COLLECTION)
        stage = "verify_vector_readback"
        verified = {r["id"]: r for r in _index_rows(raw, binding, identity, 1000)}
        if set(verified) != {r["id"] for r in records} or any(verified.get(r["id"]) != r for r in records):
            raise ValueError("incomplete vector projection; replay is safe")
        return {"status": "ready", **binding, **identity, "collection": VECTOR_COLLECTION,
                "chunks": len(records), "upserted": len(needed), "truncated": False,
                "excluded_fact_kinds": ["salary_reference"]}
    except Exception as exc:
        return {"status": "partial", **binding, "collection": VECTOR_COLLECTION,
                "reason": type(exc).__name__, "stage": stage, "truncated": False,
                "next_action": "check_bound_facts_schema_and_pinned_model_then_replay_current_sql_vector_projection"}


def search_vectors(milvus, payload, query, embedding=None, embedding_revision=None, top_k=5):
    clean, binding = _validated(payload)
    _text(query, limit=2000)
    if type(top_k) is not int or not 1 <= top_k <= 20:
        raise ValueError("top_k must be 1..20")
    stage = "model_identity"
    try:
        identity = _embedding_identity(embedding, embedding_revision)
        stage = "bounded_source_facts"
        desired = {r["id"]: r for r in _records(clean, binding, identity)}
        stage = "connect_vector_store"
        raw = _raw_milvus(milvus)
        stage = "vector_schema"
        _ensure_vector_schema(raw, create=False)
        stage = "verify_current_vector_supply"
        # Check complete supply before interpreting an ANN miss as no relevant fact.
        supplied = {r["id"]: r for r in _index_rows(raw, binding, identity, 1000)}
        if set(supplied) != set(desired) or any(supplied[k] != desired[k] for k in desired):
            raise ValueError("current SQL vector projection incomplete")
        stage = "load_pinned_offline_model"
        embedding = embedding or _pinned_embedding()
        if _embedding_identity(embedding, embedding_revision) != identity:
            raise ValueError("embedding identity changed during search")
        stage = "encode_query"
        vector = _encode(embedding, [query])[0]
        stage = "search_current_public_facts"
        batches = raw.search(collection_name=VECTOR_COLLECTION, data=[vector],
                             filter=_filter(binding, identity), limit=top_k,
                             output_fields=list(VECTOR_OUTPUT_FIELDS), consistency_level="Strong")
        results, rejected, seen = [], 0, set()
        for hit in batches[0] if batches else []:
            entity = hit.get("entity") or {}
            key = str(hit.get("id") or entity.get("id") or "")
            expected = desired.get(key)
            if expected is None or any(entity.get(k) != v for k, v in expected.items() if k != "id"):
                rejected += 1
                continue
            distance = hit.get("distance")
            if type(distance) not in (float, int) or not math.isfinite(distance) or key in seen:
                rejected += 1
                continue
            seen.add(key)
            results.append({**expected, "distance": distance, "match_eligible": False,
                            "score_kind": "retrieval_similarity_only"})
        return {"status": "partial" if rejected else "ready" if results else "empty",
                **binding, **identity, "results": results, "rejected_unverified_hits": rejected,
                "coverage": "current_structured_public_facts", "truncated": False}
    except Exception as exc:
        return {"status": "partial", **binding, "results": [], "reason": type(exc).__name__,
                "stage": stage,
                "next_action": "check_pinned_model_and_replay_current_sql_vector_projection"}
