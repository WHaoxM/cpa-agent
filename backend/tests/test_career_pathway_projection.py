"""Isolated publisher contracts. No database, network or model loading allowed.

The real Neo4j transaction and PyMilvus schema APIs are adapted by bounded test
doubles. Live services and actual model acceptance belong to the explicit import
workflow; these tests do not claim that acceptance.
"""
from copy import deepcopy
import json
import sys
from types import SimpleNamespace

import pytest
from pymilvus import MilvusClient

from app.services import career_pathway_projection as p


@pytest.fixture
def payload():
    evidence = {"url": "https://docs.python.org/3/tutorial/", "note": "Reviewed recommendation, not a source prerequisite assertion."}
    return {
        "schema_version": p.SCHEMA_VERSION, "role_id": "backend-development",
        "role_name": "Backend development", "revision": "backend-20261022-v1",
        "skills": [
            {"skill_id": 1, "name": "Python", "tier": "foundation", "requirement_kind": "recommended", "evidence": deepcopy(evidence)},
            {"skill_id": 28, "name": "SQL", "tier": "application", "requirement_kind": "recommended", "evidence": deepcopy(evidence)},
        ],
        "courses": [{"course_id": "curated_python313_intro", "name": "Python tutorial", "skill_ids": [1, 28], "chapter_ids": [101, 102], "source_url": evidence["url"]}],
        "prerequisites": [{"from_skill_id": 1, "to_skill_id": 28, "evidence": deepcopy(evidence)}],
        "jobs": [{"fact_id": "official-job-1", "title": "Backend engineer", "employer": "Official employer", "locations": ["Shanghai"], "source_uri": "https://example.org/jobs/1", "source_fetched_at": "2026-10-22", "summary": "Python API and SQL service development", "requirements": [{"kind": "skill", "label": "Python", "values": ["Python"]}], "usage": "reference_only", "scoring_ready": False}],
        "salary_references": [{"label": "salary excluded from embeddings"}],
    }


class GraphResult(list):
    def single(self):
        return self[0] if self else None

    def consume(self):
        return None


class Graph:
    def __init__(self):
        self.revisions = {}
        self.calls = []
        self.fail_verify = False

    def session(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute_write(self, function):
        saved = deepcopy(self.revisions)
        try:
            return function(self)
        except Exception:
            self.revisions = saved
            raise

    def execute_query(self, statement, params, **kwargs):
        return list(self.run(statement, params))

    def run(self, statement, params):
        self.calls.append(statement)
        if statement in p.GRAPH_CONSTRAINTS:
            return GraphResult()
        key = (params["source_id"], params["role_id"], params["revision"])
        if statement == p.GRAPH_READ:
            row = self.revisions.get(key)
            if self.fail_verify or row is None or row["hash"] != params["content_sha256"] or row["state"] != "verified":
                return GraphResult()
            return GraphResult([deepcopy({k: row[k] for k in ("payload_json", "skills", "courses", "prerequisites", "covers")})])
        if "RETURN r.content_sha256" in statement:
            row = self.revisions.setdefault(key, {"hash": params["content_sha256"], "state": "building"})
            return GraphResult([{"content_sha256": row["hash"]}])
        row = self.revisions[key]
        if "SET r.payload_json=" in statement:
            row["payload_json"] = params["payload_json"]
            row["skills"] = deepcopy(params["skills"])
        elif "UNWIND $courses" in statement:
            row["courses"] = deepcopy(params["courses"])
        elif "UNWIND $prerequisites" in statement:
            row["prerequisites"] = deepcopy(params["prerequisites"])
        elif "UNWIND $covers" in statement:
            row["covers"] = deepcopy(params["covers"])
        elif "SET r.state='verified'" in statement:
            row["state"] = "verified"
        else:
            raise AssertionError("unexpected graph operation")
        return GraphResult()


class Embedding:
    model_name = p.MODEL_NAME
    embedding_revision = p.MODEL_REVISION

    def __init__(self, vector=None):
        self.calls = []
        self.vector = vector if vector is not None else [1.0] + [0.0] * 1023

    def encode(self, texts, normalize, batch_size):
        assert normalize is True and batch_size == 16
        self.calls.append(texts)
        return [self.vector[:] for _ in texts]


class Vectors:
    create_schema = staticmethod(MilvusClient.create_schema)
    prepare_index_params = staticmethod(MilvusClient.prepare_index_params)

    def __init__(self):
        self.schema = None
        self.rows = {}
        self.calls = []
        self.hit_override = None
        self.fail_after_one = False

    def has_collection(self, name):
        self.calls.append(("has_collection", name))
        return self.schema is not None

    def create_collection(self, collection_name, schema, index_params):
        assert collection_name == p.VECTOR_COLLECTION
        self.calls.append(("create", collection_name))
        self.schema = schema.to_dict()

    def describe_collection(self, name):
        return self.schema

    def load_collection(self, name):
        self.calls.append(("load", name))

    def query(self, collection_name, filter, output_fields, limit, consistency_level):
        self.calls.append(("query", collection_name, filter))
        assert consistency_level == "Strong"
        conditions = {key: json.loads(value) for key, value in (part.split(" == ", 1) for part in filter.split(" and "))}
        return [{key: row[key] for key in output_fields} for row in self.rows.values() if all(row.get(k) == v for k, v in conditions.items())][:limit]

    def upsert(self, collection_name, data):
        self.calls.append(("upsert", collection_name, len(data)))
        for row in data:
            self.rows[row["id"]] = deepcopy(row)
            if self.fail_after_one:
                self.fail_after_one = False
                raise RuntimeError("simulated partial remote batch")

    def flush(self, name):
        self.calls.append(("flush", name))

    def search(self, collection_name, data, filter, limit, output_fields, consistency_level):
        self.calls.append(("search", collection_name, filter))
        if self.hit_override is not None:
            return [self.hit_override]
        rows = self.query(collection_name, filter, output_fields, limit, consistency_level)
        return [[{"id": row["id"], "distance": 0.9, "entity": row} for row in rows]]


def test_digest_excludes_only_documented_read_fields(payload):
    digest = p.canonical_payload_digest(payload)
    payload.update(content_sha256=digest, published_at="now", projection_status={"graph": "partial"})
    assert p.canonical_payload_digest(payload) == digest
    payload["role_name"] = "Changed business fact"
    assert p.canonical_payload_digest(payload) != digest
    with pytest.raises(ValueError, match="digest mismatch"):
        p.project_graph(None, payload)
    payload["unexpected"] = "not a snapshot field"
    with pytest.raises(ValueError, match="unknown"):
        p.canonical_payload_digest(payload)


def test_sql_digest_agrees_and_captured_source_hash_is_bound(payload):
    from app.services.career_pathway import content_digest
    payload["jobs"][0]["source_sha256"] = "a" * 64
    first = p.canonical_payload_digest(payload)
    assert first == content_digest(payload)
    payload.update(content_sha256=first, published_at="now", projection_status={})
    assert first == p.canonical_payload_digest(payload) == content_digest(payload)
    payload["jobs"][0]["source_sha256"] = "b" * 64
    assert first != p.canonical_payload_digest(payload) == content_digest(payload)


def test_revision_is_a_string_business_identity(payload):
    payload["revision"] = 1
    with pytest.raises(ValueError, match="string revision"):
        p.project_graph(None, payload)


@pytest.mark.parametrize("mutation", ["missing_skill", "duplicate", "cycle", "scoring", "credential_url"])
def test_invalid_payload_never_reaches_external_service(payload, mutation):
    if mutation == "missing_skill":
        payload["courses"][0]["skill_ids"] = [999]
    elif mutation == "duplicate":
        payload["skills"].append(deepcopy(payload["skills"][0]))
    elif mutation == "cycle":
        payload["prerequisites"].append({"from_skill_id": 28, "to_skill_id": 1, "evidence": payload["skills"][0]["evidence"]})
    elif mutation == "scoring":
        payload["jobs"][0]["scoring_ready"] = True
    else:
        payload["skills"][0]["evidence"]["url"] = "https://user:password@example.invalid/"
    with pytest.raises(ValueError):
        p.project_vectors(None, payload)


def test_graph_schema_is_explicit_independent_and_non_destructive():
    graph = Graph()
    assert p.ensure_graph_schema(graph)["constraints"] == 4
    assert len(graph.calls) == 4
    assert all("CareerPathway" in query and "DELETE" not in query for query in graph.calls)


def test_graph_transaction_idempotent_immutable_and_current_sql_bound(payload):
    graph = Graph()
    assert p.project_graph(graph, payload)["status"] == "ready"
    assert p.project_graph(graph, payload)["status"] == "ready"
    assert len(graph.revisions) == 1
    read = p.read_graph(graph, payload)
    assert read["skills"] == payload["skills"]
    assert read["courses"] == payload["courses"]
    assert read["relation_semantics"] == "prerequisite_to_dependent"
    changed = deepcopy(payload)
    changed["role_name"] = "Different fact, same revision"
    assert p.project_graph(graph, changed)["status"] == "partial"
    assert p.read_graph(graph, changed)["status"] == "unavailable"
    changed["revision"] = "backend-20261022-v2"
    assert p.read_graph(graph, changed)["status"] == "unavailable"
    assert p.project_graph(graph, changed)["status"] == "ready"
    assert len(graph.revisions) == 2
    assert p.read_graph(graph, payload)["status"] == "ready"
    assert not any("DELETE" in query or "CREATE CONSTRAINT" in query for query in graph.calls)


def test_graph_verification_failure_rolls_back_and_read_rejects_tampering(payload):
    graph = Graph()
    graph.fail_verify = True
    assert p.project_graph(graph, payload)["status"] == "partial"
    assert graph.revisions == {}
    graph.fail_verify = False
    p.project_graph(graph, payload)
    row = next(iter(graph.revisions.values()))
    row["courses"][0]["chapter_ids"] = [999]
    assert p.read_graph(graph, payload)["status"] == "unavailable"


def test_vector_schema_pinned_public_facts_and_idempotent_revisions(payload):
    store, model = Vectors(), Embedding()
    result = p.project_vectors(store, payload, model)
    assert result["status"] == "ready" and result["chunks"] == 5
    assert len(model.calls) == 1
    fields = {f["name"]: f for f in store.schema["fields"]}
    assert fields["embedding"]["params"]["dim"] == 1024
    assert store.schema["enable_dynamic_field"] is False
    assert set(p.VECTOR_OUTPUT_FIELDS) <= set(fields)
    assert {r["kind"] for r in store.rows.values()} == {"skill", "course", "prerequisite", "job_reference"}
    assert all(r["visibility"] == "public" and r["embedding_revision"] == p.MODEL_REVISION for r in store.rows.values())
    assert all("salary excluded" not in r["text"] for r in store.rows.values())
    assert p.project_vectors(store, payload, model)["upserted"] == 0
    assert len(model.calls) == 1
    payload["revision"] = "backend-20261022-v2"
    assert p.project_vectors(store, payload, model)["status"] == "ready"
    assert len(store.rows) == 10
    assert all(call[1] == p.VECTOR_COLLECTION for call in store.calls)


def test_partial_vector_batch_is_explicit_and_replay_only_encodes_missing(payload):
    store, model = Vectors(), Embedding()
    store.fail_after_one = True
    assert p.project_vectors(store, payload, model)["status"] == "partial"
    assert len(store.rows) == 1
    result = p.project_vectors(store, payload, model)
    assert result["status"] == "ready" and result["upserted"] == 4
    assert len(model.calls[-1]) == 4


@pytest.mark.parametrize("vector", [[1.0], [0.0] * 1024, [float("nan")] * 1024])
def test_invalid_embeddings_have_no_fallback_or_upsert(payload, vector):
    store = Vectors()
    assert p.project_vectors(store, payload, Embedding(vector))["status"] == "partial"
    assert store.rows == {}
    assert not any(call[0] == "upsert" for call in store.calls)


def test_wrong_model_or_weights_rejected_before_collection_calls(payload):
    store, model = Vectors(), Embedding()
    model.embedding_revision = "main"
    assert p.project_vectors(store, payload, model)["status"] == "partial"
    assert store.calls == []
    model.embedding_revision = p.MODEL_REVISION
    model.local_snapshot_path = "/snapshots/other-weights"
    assert p.project_vectors(store, payload, model)["status"] == "partial"
    assert store.calls == []


def test_oversized_fact_requires_source_split_without_truncation(payload):
    store, model = Vectors(), Embedding()
    payload["jobs"][0]["summary"] = "x" * 2000
    result = p.project_vectors(store, payload, model)
    assert result["status"] == "partial" and result["truncated"] is False
    assert store.calls == [] and model.calls == []


def test_existing_incompatible_schema_is_not_migrated(payload):
    store, model = Vectors(), Embedding()
    assert p.project_vectors(store, payload, model)["status"] == "ready"
    original = deepcopy(store.rows)
    next(f for f in store.schema["fields"] if f["name"] == "embedding")["params"]["dim"] = 768
    assert p.project_vectors(store, payload, model)["status"] == "partial"
    assert store.rows == original


def test_search_returns_verified_current_facts_and_never_scoring(payload):
    store, model = Vectors(), Embedding()
    assert p.project_vectors(store, payload, model)["status"] == "ready"
    result = p.search_vectors(store, payload, "Python API", model)
    assert result["status"] == "ready" and len(result["results"]) == 5
    assert all(r["match_eligible"] is False and r["score_kind"] == "retrieval_similarity_only" for r in result["results"])
    expression = next(c[2] for c in store.calls if c[0] == "search")
    for key in ("source_id", "role_id", "revision", "content_sha256", "embedding_revision", "visibility"):
        assert key + " == " in expression
    payload["revision"] = "backend-20261022-v2"
    assert p.search_vectors(store, payload, "Python", model)["status"] == "partial"


def test_search_missing_projection_is_not_an_empty_success(payload):
    store, model = Vectors(), Embedding()
    assert p.search_vectors(store, payload, "Python", model)["status"] == "partial"
    assert model.calls == [] and store.schema is None


def test_search_rejects_foreign_or_modified_hits(payload):
    store, model = Vectors(), Embedding()
    p.project_vectors(store, payload, model)
    row = {k: v for k, v in next(iter(store.rows.values())).items() if k != "embedding"}
    altered = {**row, "text": "untrusted content"}
    store.hit_override = [{"id": row["id"], "distance": 0.9, "entity": row}, {"id": row["id"], "distance": 1.0, "entity": altered}, {"id": "private-or-stale", "distance": 1.0, "entity": row}]
    result = p.search_vectors(store, payload, "Python", model)
    assert result["status"] == "partial"
    assert len(result["results"]) == 1 and result["rejected_unverified_hits"] == 2
    assert result["results"][0]["text"] == row["text"]
    store.hit_override = []
    assert p.search_vectors(store, payload, "unmatched", model)["status"] == "empty"


def test_token_limit_fails_explicitly_not_silent_truncation(payload):
    store, model = Vectors(), Embedding()
    model._model = SimpleNamespace(max_seq_length=2, tokenizer=lambda texts, **kwargs: {"input_ids": [[1, 2, 3] for _ in texts]})
    assert p.project_vectors(store, payload, model)["status"] == "partial"
    assert model.calls == [] and store.rows == {}


def test_pinned_factory_uses_existing_service_without_mutating_singleton(monkeypatch, tmp_path):
    from app.services.embedding_service import EmbeddingService
    shared = EmbeddingService()
    original_model = shared._model
    snapshot = tmp_path / p.MODEL_REVISION
    snapshot.mkdir()
    (snapshot / "modules.json").write_text(json.dumps([
        {"path": "", "type": "sentence_transformers.models.Transformer"},
        {"path": "1_Pooling", "type": "sentence_transformers.models.Pooling"},
        {"path": "2_Normalize", "type": "sentence_transformers.models.Normalize"}]), encoding="utf-8")
    (snapshot / "sentence_bert_config.json").write_text(json.dumps({"max_seq_length": 8192, "do_lower_case": False}), encoding="utf-8")
    created = []
    sentinel = object()
    transformer, pooling, normalize = object(), object(), object()
    def constructor(*, modules):
        created.append(("model", modules))
        return sentinel
    def make_transformer(path, **kwargs):
        created.append((path, kwargs))
        return transformer
    fake_models = SimpleNamespace(Transformer=make_transformer,
        Pooling=SimpleNamespace(load=lambda path: pooling), Normalize=lambda: normalize)
    monkeypatch.setitem(sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=constructor, models=fake_models))
    monkeypatch.setattr(p, "_local_snapshot", lambda: snapshot)
    p._pinned_embedding.cache_clear()
    isolated = p._pinned_embedding()
    assert isolated is not shared and isinstance(isolated, EmbeddingService)
    assert isolated._model is sentinel and shared._model is original_model
    assert created == [(str(snapshot), {"max_seq_length": 8192, "do_lower_case": False,
        "model_args": {"local_files_only": True, "trust_remote_code": False},
        "tokenizer_args": {"local_files_only": True, "trust_remote_code": False}}),
        ("model", [transformer, pooling, normalize])]
    assert p._embedding_identity(isolated, None)["embedding_revision"] == p.MODEL_REVISION
    assert p._pinned_embedding() is isolated
    p._pinned_embedding.cache_clear()


def test_existing_runtime_cache_resolves_only_fixed_snapshot(monkeypatch, tmp_path):
    snapshot = tmp_path / "models--BAAI--bge-m3" / "snapshots" / p.MODEL_REVISION
    snapshot.mkdir(parents=True)
    (snapshot / "modules.json").write_text("[]", encoding="utf-8")
    monkeypatch.setenv("SENTENCE_TRANSFORMERS_HOME", str(tmp_path))
    assert p._local_snapshot() == snapshot.resolve()
