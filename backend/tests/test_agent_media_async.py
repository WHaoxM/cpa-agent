"""The upload/request threads never run PDF OCR or a repair parse."""

import importlib.util
import io
import json
import sys
import types
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.agent_runtime.fork_jobs import set_inline_for_tests
from app.services.agent_runtime.media.file_store import AgentFileService


class _Upload:
    filename = "scan.pdf"
    mimetype = "application/pdf"

    def save(self, path):
        with open(path, "wb") as handle:
            handle.write(b"%PDF-1.4 test")


def _service(tmp_path, monkeypatch):
    import app.services.agent_runtime.media.file_store as store

    monkeypatch.setattr(store, "_upload_root", lambda: tmp_path)
    monkeypatch.setattr(store, "ensure_media_schema", lambda _mysql: False)
    return AgentFileService(mysql_client=SimpleNamespace(
        execute=lambda *a, **k: None,
        fetch_one=lambda *a, **k: None,
        fetch_all=lambda *a, **k: [],
    ))


def test_enqueue_failure_never_parses_on_upload_thread(tmp_path, monkeypatch):
    import app.services.agent_runtime.media.jobs as jobs

    set_inline_for_tests(False)
    svc = _service(tmp_path, monkeypatch)
    monkeypatch.setattr(jobs, "enqueue_media_job", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("queue down")))
    monkeypatch.setattr(svc, "complete_ingest", lambda _fid: (_ for _ in ()).throw(AssertionError("sync OCR")))
    meta = svc.upload(file_storage=_Upload(), student_id="stu")
    assert meta["status"] == "failed"
    assert "queue" in (meta["error"] or "").lower()
    replay = svc.list_events(meta["file_id"])
    assert [event["status"] for event in replay["events"]] == ["queued", "failed"]
    assert [event["seq"] for event in replay["events"]] == [1, 2]
    assert (tmp_path / f"{meta['file_id']}.pdf").exists()


def test_media_events_replay_queued_ingesting_ready_with_cursor(tmp_path, monkeypatch):
    import app.services.agent_runtime.media.file_store as store
    import app.services.agent_runtime.media.jobs as jobs

    set_inline_for_tests(False)
    svc = _service(tmp_path, monkeypatch)
    monkeypatch.setattr(jobs, "enqueue_media_job", lambda kind, file_id, task_id: task_id)
    monkeypatch.setattr(store, "_pdf_capability_snapshot", lambda: {"status": "ready"})
    monkeypatch.setattr(store, "ingest_file", lambda *_a, **_k: ([
        {"kind": "full_text", "ordinal": 0, "body_text": "完整简历", "meta": {}},
        {"kind": "excerpt", "ordinal": 0, "body_text": "完整简历", "meta": {}},
    ], {"engine": "sidecar", "blocks": []}, True))

    uploaded = svc.upload(file_storage=_Upload(), student_id="stu")
    assert uploaded["event_cursor"] == 1
    svc.complete_ingest(uploaded["file_id"])

    first = svc.list_events(uploaded["file_id"], after=0, limit=2)
    assert [event["status"] for event in first["events"]] == ["queued", "ingesting"]
    assert [event["seq"] for event in first["events"]] == [1, 2]
    assert all(event["attempt"] == 1 for event in first["events"])
    assert all(event["phase"] == "ingest" for event in first["events"])
    assert all(event["occurred_at"].endswith("+00:00") for event in first["events"])
    assert first["cursor"] == 2
    assert first["has_more"] is True
    assert first["latest_seq"] == 3

    second = svc.list_events(uploaded["file_id"], after=first["cursor"], limit=2)
    assert [event["status"] for event in second["events"]] == ["ready"]
    assert second["cursor"] == 3
    assert second["has_more"] is False
    assert second["snapshot"]["status"] == "ready"
    assert second["source"] == "agent_file.ingest_meta.events"

    # Late-ack redelivery of a committed file does not duplicate public events.
    svc.complete_ingest(uploaded["file_id"])
    assert svc.list_events(uploaded["file_id"])["latest_seq"] == 3


def test_media_event_replay_fails_closed_on_corrupt_persisted_sequence(tmp_path, monkeypatch):
    from app.services.agent_runtime.media.file_store import MediaEventLogError

    svc = _service(tmp_path, monkeypatch)
    now = datetime.now().isoformat(sep=" ")
    svc._persist_file({
        "file_id": "file_bad_events", "filename": "bad.pdf", "storage_path": "bad.pdf",
        "status": "ingesting", "created_at": now, "updated_at": now,
        "ingest_meta": {"events": [{
            "seq": 2, "event_id": "bad", "status": "queued", "phase": "ingest",
            "attempt": 1, "occurred_at": now, "job_id": None,
        }]},
    })
    with pytest.raises(MediaEventLogError, match="contiguous"):
        svc.list_events("file_bad_events")


def test_upgrade_is_queued_without_replacing_old_text(tmp_path, monkeypatch):
    import app.services.agent_runtime.media.jobs as jobs

    set_inline_for_tests(False)
    svc = _service(tmp_path, monkeypatch)
    path = tmp_path / "old.pdf"
    path.write_bytes(b"%PDF-1.4 test")
    svc._persist_file({
        "file_id": "file_old", "student_id": "stu", "session_id": "s", "filename": "old.pdf",
        "storage_path": str(path), "status": "ready", "error": None, "ingest_meta": {"engine": "mineru"},
    })
    svc._persist_artifacts("file_old", [{"kind": "full_text", "ordinal": 0, "body_text": "old OCR",
        "meta": {"truncated": True}}])
    calls = []
    monkeypatch.setattr(
        jobs,
        "enqueue_media_job",
        lambda kind, file_id, task_id: calls.append((kind, file_id, task_id)),
    )
    monkeypatch.setattr(svc, "upgrade_thin_ingest", lambda _fid: (_ for _ in ()).throw(AssertionError("sync repair")))
    meta = svc.schedule_upgrade("file_old")
    assert meta["status"] == "ready"
    assert meta["upgrade_status"] == "queued"
    assert svc.get_artifact("file_old", "full_text") == "old OCR"
    assert calls[0][:2] == ("upgrade", "file_old")
    svc.schedule_upgrade("file_old")
    assert len(calls) == 1


def test_upgrade_event_attempt_increments_only_on_explicit_retry(tmp_path, monkeypatch):
    import app.services.agent_runtime.media.jobs as jobs

    set_inline_for_tests(False)
    svc = _service(tmp_path, monkeypatch)
    path = tmp_path / "old.pdf"
    path.write_bytes(b"%PDF-1.4 test")
    svc._persist_file({
        "file_id": "file_attempts", "student_id": "stu", "filename": "old.pdf",
        "storage_path": str(path), "status": "ready", "error": None,
        "ingest_meta": {"engine": "mineru"},
    })
    svc._persist_artifacts("file_attempts", [{
        "kind": "full_text", "ordinal": 0, "body_text": "old OCR",
        "meta": {"truncated": True},
    }])
    monkeypatch.setattr(jobs, "enqueue_media_job", lambda kind, file_id, task_id: task_id)
    assert svc.schedule_upgrade("file_attempts")["upgrade_status"] == "queued"
    monkeypatch.setattr(svc, "upgrade_thin_ingest", lambda _fid: (_ for _ in ()).throw(
        RuntimeError("OCR unavailable")
    ))
    assert svc.complete_upgrade("file_attempts")["upgrade_status"] == "failed"

    first = svc.list_events("file_attempts")
    assert [(e["status"], e["attempt"]) for e in first["events"]] == [
        ("queued", 1), ("ingesting", 1), ("failed", 1),
    ]
    assert all(event["phase"] == "upgrade" for event in first["events"])

    assert svc.schedule_upgrade("file_attempts", force=True)["upgrade_status"] == "queued"
    replay = svc.list_events("file_attempts", after=first["cursor"])
    assert [(e["status"], e["attempt"]) for e in replay["events"]] == [("queued", 2)]


def test_stale_ingest_becomes_retryable_failure(tmp_path, monkeypatch):
    svc = _service(tmp_path, monkeypatch)
    old = (datetime.now() - timedelta(hours=2)).isoformat(sep=" ")
    path = tmp_path / "old.pdf"
    path.write_bytes(b"%PDF-1.4 test")
    svc._persist_file({
        "file_id": "file_stale", "filename": "old.pdf", "storage_path": str(path),
        "status": "ingesting", "ingest_meta": {"ingest_queued_at": old},
        "created_at": old, "updated_at": old,
    })
    meta = svc.get("file_stale")
    assert meta["status"] == "failed"
    assert "可重试" in meta["error"]
    assert path.is_file()


def test_durable_celery_job_is_not_failed_by_flask_restart_timeout(tmp_path, monkeypatch):
    from app.services.agent_runtime.media.jobs import build_media_job_id

    svc = _service(tmp_path, monkeypatch)
    old = (datetime.now() - timedelta(hours=2)).isoformat(sep=" ")
    path = tmp_path / "queued.pdf"
    path.write_bytes(b"%PDF-1.4 test")
    job_id = build_media_job_id("ingest", "file_durable", 1)
    svc._persist_file({
        "file_id": "file_durable", "filename": "queued.pdf", "storage_path": str(path),
        "status": "ingesting", "ingest_meta": {
            "ingest_queued_at": old,
            "job_backend": "celery",
            "job_id": job_id,
            "job_status": "queued",
        },
        "created_at": old, "updated_at": old,
    })
    meta = svc.get("file_durable")
    assert meta["status"] == "ingesting"
    assert meta["job_id"] == job_id
    assert meta["job_status"] == "queued"


def test_celery_delivery_contract_is_late_ack_and_single_prefetch():
    from app.services.agent_runtime.media.jobs import MEDIA_QUEUE, celery_app, media_task

    assert celery_app.conf.task_default_queue == MEDIA_QUEUE
    assert celery_app.conf.task_acks_late is True
    assert celery_app.conf.task_reject_on_worker_lost is True
    assert celery_app.conf.worker_prefetch_multiplier == 1
    assert celery_app.conf.broker_transport_options["visibility_timeout"] == 6 * 60 * 60
    assert media_task.acks_late is True
    assert media_task.reject_on_worker_lost is True


def test_enqueue_uses_persisted_deterministic_task_id(monkeypatch):
    from app.services.agent_runtime.media import jobs

    sent = []
    monkeypatch.setattr(
        jobs.media_task,
        "apply_async",
        lambda **kwargs: sent.append(kwargs),
    )
    task_id = jobs.build_media_job_id("ingest", "file_123", 1)
    assert jobs.enqueue_media_job("ingest", "file_123", task_id=task_id) == task_id
    assert sent == [{
        "args": ["ingest", "file_123"],
        "task_id": task_id,
        "queue": jobs.MEDIA_QUEUE,
        "retry": False,
    }]


def test_worker_recovers_queued_record_from_disk_after_memory_reset(tmp_path, monkeypatch):
    import app.services.agent_runtime.media.file_store as store

    svc = _service(tmp_path, monkeypatch)
    path = tmp_path / "resume.txt"
    path.write_text("教育经历\n项目经历\nPython", encoding="utf-8")
    now = datetime.now().isoformat(sep=" ")
    svc._persist_file({
        "file_id": "file_restart", "student_id": "stu", "filename": "resume.txt",
        "storage_path": str(path), "status": "ingesting", "purpose": "chat_attachment",
        "ingest_meta": {
            "ingest_queued_at": now,
            "job_backend": "celery",
            "job_id": "cpa-media-ingest-file_restart-1",
            "job_status": "queued",
        },
        "created_at": now, "updated_at": now,
    })
    # Simulate a new worker process: no process-local file/artifact cache.
    store._mem_files.clear()
    store._mem_artifacts.clear()
    recovered = AgentFileService(mysql_client=svc.mysql)
    result = recovered.complete_ingest("file_restart")
    assert result["status"] == "ready"
    assert result["job_status"] == "completed"
    assert "Python" in recovered.get_artifact("file_restart", "full_text")

    # A late-ack redelivery after commit is a no-op, not a duplicate parse.
    monkeypatch.setattr(store, "ingest_file", lambda *_a, **_k: (_ for _ in ()).throw(
        AssertionError("ready record must not parse again")
    ))
    assert recovered.complete_ingest("file_restart")["status"] == "ready"


def test_catalog_replace_failure_preserves_previous_authoritative_json(tmp_path, monkeypatch):
    from app.services.agent_runtime.media.file_store import _atomic_json_write

    catalog = tmp_path / "file.meta.json"
    catalog.write_text('{"status":"queued"}', encoding="utf-8")

    def fail_replace(_self, _target):
        raise OSError("simulated worker loss before replace")

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="worker loss"):
        _atomic_json_write(catalog, {"status": "ready"})
    assert json.loads(catalog.read_text(encoding="utf-8"))["status"] == "queued"
    assert not list(tmp_path.glob("*.tmp"))


def test_flask_poll_observes_worker_disk_commit_instead_of_stale_memory(tmp_path, monkeypatch):
    svc = _service(tmp_path, monkeypatch)
    now = datetime.now().isoformat(sep=" ")
    record = {
        "file_id": "file_cross_process", "filename": "resume.pdf",
        "storage_path": str(tmp_path / "resume.pdf"), "status": "ingesting",
        "ingest_meta": {"job_backend": "celery", "job_id": "job-1", "job_status": "queued"},
        "created_at": now, "updated_at": now,
    }
    svc._persist_file(record)
    assert svc.get("file_cross_process")["status"] == "ingesting"

    # A different process cannot update this process's _mem_files; it only
    # atomically replaces the shared authoritative catalog.
    disk = tmp_path / "file_cross_process.meta.json"
    committed = {**record, "status": "ready", "ingest_meta": {
        **record["ingest_meta"], "job_status": "completed",
    }}
    disk.write_text(json.dumps(committed, ensure_ascii=False), encoding="utf-8")
    assert svc.get("file_cross_process")["status"] == "ready"
    assert svc.get("file_cross_process")["job_status"] == "completed"


def test_artifact_commit_failure_cannot_publish_false_ready(tmp_path, monkeypatch):
    import app.services.agent_runtime.media.file_store as store

    svc = _service(tmp_path, monkeypatch)
    path = tmp_path / "resume.txt"
    path.write_text("Python 项目经历", encoding="utf-8")
    now = datetime.now().isoformat(sep=" ")
    svc._persist_file({
        "file_id": "file_commit_failure", "filename": "resume.txt",
        "storage_path": str(path), "status": "ingesting", "purpose": "chat_attachment",
        "ingest_meta": {"job_backend": "celery", "job_id": "job-commit", "job_status": "queued"},
        "created_at": now, "updated_at": now,
    })
    real_write = store._atomic_json_write

    def fail_artifacts(catalog, value, **kwargs):
        if catalog.name.endswith(".artifacts.json"):
            raise OSError("artifact disk full")
        return real_write(catalog, value, **kwargs)

    monkeypatch.setattr(store, "_atomic_json_write", fail_artifacts)
    result = svc.complete_ingest("file_commit_failure")
    assert result["status"] == "failed"
    assert result["job_status"] == "failed"
    assert "artifact disk full" in result["error"]


def test_stale_upgrade_preserves_old_text_and_force_requeues(tmp_path, monkeypatch):
    import app.services.agent_runtime.media.jobs as jobs

    set_inline_for_tests(False)
    svc = _service(tmp_path, monkeypatch)
    old = (datetime.now() - timedelta(hours=2)).isoformat(sep=" ")
    path = tmp_path / "old.pdf"
    path.write_bytes(b"%PDF-1.4 test")
    svc._persist_file({
        "file_id": "file_stale_upgrade", "filename": "old.pdf", "storage_path": str(path),
        "status": "ready", "ingest_meta": {"upgrade_status": "running", "upgrade_started_at": old},
        "created_at": old, "updated_at": old,
    })
    svc._persist_artifacts("file_stale_upgrade", [{
        "kind": "full_text", "ordinal": 0, "body_text": "old OCR", "meta": {"truncated": True},
    }])
    meta = svc.get("file_stale_upgrade")
    assert meta["status"] == "ready"
    assert meta["upgrade_status"] == "failed"
    assert svc.get_artifact("file_stale_upgrade", "full_text") == "old OCR"
    calls = []
    monkeypatch.setattr(
        jobs,
        "enqueue_media_job",
        lambda kind, file_id, task_id: calls.append((kind, file_id, task_id)),
    )
    assert svc.schedule_upgrade("file_stale_upgrade")["upgrade_status"] == "failed"
    assert calls == []
    assert svc.schedule_upgrade("file_stale_upgrade", force=True)["upgrade_status"] == "queued"
    assert calls[0][:2] == ("upgrade", "file_stale_upgrade")


def test_stub_cannot_advertise_ocr_ready(monkeypatch):
    from app.pipeline.entity_extractor import pdf_sidecar

    class Response:
        status_code = 200

        def json(self):
            return {"status": "ok", "engine": "stub", "ocr_pdf": False}

    class Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def get(self, _url):
            return Response()

    import httpx
    monkeypatch.setattr(httpx, "Client", Client)
    monkeypatch.setenv("CPA_L0_PARSE_SIDECAR_URL", "http://localhost:9380")
    status, reason = pdf_sidecar.probe_sidecar()
    assert status != "ready"
    assert "ocr" in reason.lower() or "stub" in reason.lower()


def test_sidecar_health_body_cannot_claim_ready_when_status_is_unavailable(monkeypatch):
    from app.pipeline.entity_extractor import pdf_sidecar

    class Response:
        status_code = 200

        def json(self):
            return {"status": "unavailable", "engine": "mineru", "ocr_pdf": True}

    class Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def get(self, _url):
            return Response()

    import httpx
    monkeypatch.setattr(httpx, "Client", Client)
    monkeypatch.setenv("CPA_L0_PARSE_SIDECAR_URL", "http://localhost:9380")
    status, reason = pdf_sidecar.probe_sidecar()
    assert status == "disabled"
    assert "unavailable" in reason


def _load_real_sidecar():
    path = Path(__file__).resolve().parents[1] / "scripts" / "parse_sidecar_mineru.py"
    spec = importlib.util.spec_from_file_location("parse_sidecar_mineru_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_real_sidecar_reports_unavailable_without_local_models(monkeypatch):
    sidecar = _load_real_sidecar()
    monkeypatch.setattr(sidecar, "find_pipeline_kit_root", lambda: None)
    client = sidecar.app.test_client()
    health = client.get("/v1/health")
    assert health.status_code == 503
    assert health.get_json()["ocr_pdf"] is False
    assert health.get_json()["durable_tasks"] is False
    assert health.get_json()["task_mode"] == "synchronous"
    parse = client.post("/api/cpa/parse", data={"file": (io.BytesIO(b"%PDF-1.4"), "resume.pdf")})
    assert parse.status_code == 503
    assert not parse.get_json().get("text")


def test_real_sidecar_rejects_non_ocr_fallback(monkeypatch):
    sidecar = _load_real_sidecar()
    monkeypatch.setattr(sidecar, "_capability", lambda: (True, "ok"))
    from app.pipeline.entity_extractor import l0_full_parser

    class FakeParser:
        def parse_file(self, _path):
            return SimpleNamespace(success=True, engine="pymupdf", text="text layer", error=None)

    monkeypatch.setattr(l0_full_parser, "L0FullParser", FakeParser)
    response = sidecar.app.test_client().post(
        "/api/cpa/parse", data={"file": (io.BytesIO(b"%PDF-1.4"), "resume.pdf")}
    )
    assert response.status_code == 503
    assert not response.get_json().get("text")


def test_real_sidecar_uses_distinct_input_stems_per_request(monkeypatch):
    sidecar = _load_real_sidecar()
    monkeypatch.setattr(sidecar, "_capability", lambda: (True, "ok"))
    from app.pipeline.entity_extractor import l0_full_parser

    stems = []

    class FakeParser:
        def parse_file(self, path):
            stems.append(Path(path).stem)
            return SimpleNamespace(
                success=True, engine="mineru", text="第 1 页", error=None,
                blocks=[SimpleNamespace(text="第 1 页", page=0, block_type="TEXT")],
            )

    monkeypatch.setattr(l0_full_parser, "L0FullParser", FakeParser)
    client = sidecar.app.test_client()
    for _ in range(2):
        response = client.post(
            "/api/cpa/parse", data={"file": (io.BytesIO(b"%PDF-1.4"), "resume.pdf")}
        )
        assert response.status_code == 200
    assert len(set(stems)) == 2


def test_sidecar_l0_preserves_page_numbered_blocks(monkeypatch):
    from app.pipeline.entity_extractor import l0_full_parser
    from app.pipeline.entity_extractor import pdf_sidecar

    monkeypatch.setattr(pdf_sidecar, "parse_pdf_via_sidecar", lambda _path: {
        "success": True, "engine": "mineru", "text": "第一页\n第二页",
        "blocks": [
            {"block_id": "a", "type": "TEXT", "text": "第一页", "page": 0},
            {"block_id": "b", "type": "TEXT", "text": "第二页", "page": 1},
        ],
    })
    result = l0_full_parser.L0FullParser()._parse_pdf_sidecar("resume.pdf")
    assert result.success
    assert len(result.blocks) == 2
    assert [block.page for block in result.blocks] == [0, 1]


def test_sidecar_rejects_text_layer_fallback_response(tmp_path, monkeypatch):
    from app.pipeline.entity_extractor import pdf_sidecar

    class Response:
        status_code = 200
        headers = {"content-type": "application/json"}

        def json(self):
            return {"engine": "pymupdf", "text": "not OCR"}

    class Client:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def post(self, *_args, **_kwargs):
            return Response()

    import httpx
    monkeypatch.setattr(httpx, "Client", Client)
    monkeypatch.setenv("CPA_L0_PARSE_SIDECAR_URL", "http://localhost:9380")
    path = tmp_path / "sample.pdf"
    path.write_bytes(b"%PDF-1.4")
    response = pdf_sidecar.parse_pdf_via_sidecar(str(path))
    assert response["success"] is False
    assert response["text"] == ""


def test_mineru_derivative_directories_are_isolated_and_cleaned(monkeypatch):
    from app.pipeline.entity_extractor.l0_full_parser import L0Block, L0FullParser

    class FakeDoc:
        def __len__(self):
            return 1

        def close(self):
            pass

    pymupdf = types.ModuleType("pymupdf")
    pymupdf.open = lambda _path: FakeDoc()
    common = types.ModuleType("mineru.cli.common")
    common.read_fn = lambda _path, _kind: b"PDF"
    outputs = []

    def fake_parse(**kwargs):
        root = Path(kwargs["output_dir"])
        outputs.append(root)
        (root / "resume_content_list.json").write_text(json.dumps([{"text": "ok"}]))

    common.do_parse = fake_parse
    monkeypatch.setitem(sys.modules, "pymupdf", pymupdf)
    monkeypatch.setitem(sys.modules, "mineru.cli.common", common)
    parser = L0FullParser()
    monkeypatch.setattr(parser, "_mineru_json_to_blocks", lambda _data: [
        L0Block("b1", "TEXT", "ok", 0.9, "mineru", page=0),
    ])
    assert parser._parse_pdf_mineru("resume.pdf").success
    assert parser._parse_pdf_mineru("resume.pdf").success
    assert outputs[0] != outputs[1]
    assert all(not path.exists() for path in outputs)


def test_retry_api_requires_auth_and_owner_before_queueing(monkeypatch):
    from flask import Flask
    from app.api.agent_media import agent_retry_file
    from app.config import Config
    from app.services.agent_runtime.media import file_store

    calls = []

    class FakeService:
        def get(self, _file_id):
            return {"file_id": "file_owned", "student_id": "other_stu"}

        def schedule_upgrade(self, file_id, *, force=False):
            calls.append((file_id, force))
            return {"file_id": file_id, "status": "ready", "upgrade_status": "queued"}

    svc = FakeService()
    monkeypatch.setattr(file_store, "get_agent_file_service", lambda: svc)
    app = Flask(__name__)

    monkeypatch.setattr(Config, "DEMO_MODE", False)
    with app.test_request_context("/api/agent/files/file_owned/retry", method="POST"):
        response, status = agent_retry_file("file_owned")
    assert status == 401
    assert not calls

    monkeypatch.setattr(Config, "DEMO_MODE", True)
    monkeypatch.setattr(Config, "DEMO_STUDENT_ID", "stu_scope")
    with app.test_request_context("/api/agent/files/file_owned/retry", method="POST"):
        response, status = agent_retry_file("file_owned")
    assert status == 403
    assert not calls

    monkeypatch.setattr(svc, "get", lambda _file_id: {
        "file_id": "file_owned", "student_id": "stu_scope",
    })
    with app.test_request_context("/api/agent/files/file_owned/retry", method="POST"):
        response = agent_retry_file("file_owned")
    assert response.get_json()["data"]["upgrade_status"] == "queued"
    assert calls == [("file_owned", True)]


def test_retry_api_route_is_registered():
    from app import create_app

    app = create_app()
    assert "/api/agent/files/<file_id>/retry" in {
        str(rule) for rule in app.url_map.iter_rules()
    }


def test_media_events_api_enforces_owner_and_passes_cursor(monkeypatch):
    from flask import Flask
    from app.api.agent_media import agent_get_file_events
    from app.config import Config
    from app.services.agent_runtime.media import file_store

    calls = []

    class FakeService:
        def get(self, _file_id):
            return {"file_id": "file_owned", "student_id": "other_stu"}

        def list_events(self, file_id, *, after, limit):
            calls.append((file_id, after, limit))
            return {
                "file_id": file_id,
                "events": [{"seq": after + 1, "status": "ready"}],
                "cursor": after + 1,
                "has_more": False,
                "latest_seq": after + 1,
                "snapshot": {"status": "ready"},
                "source": "agent_file.ingest_meta.events",
            }

    svc = FakeService()
    monkeypatch.setattr(file_store, "get_agent_file_service", lambda: svc)
    monkeypatch.setattr(Config, "DEMO_MODE", True)
    monkeypatch.setattr(Config, "DEMO_STUDENT_ID", "stu_scope")
    app = Flask(__name__)

    with app.test_request_context("/api/agent/files/file_owned/events?after=7&limit=20"):
        response, status = agent_get_file_events("file_owned")
    assert status == 403
    assert calls == []

    monkeypatch.setattr(svc, "get", lambda _file_id: {
        "file_id": "file_owned", "student_id": "stu_scope",
    })
    with app.test_request_context("/api/agent/files/file_owned/events?after=7&limit=20"):
        response = agent_get_file_events("file_owned")
    payload = response.get_json()["data"]
    assert payload["cursor"] == 8
    assert calls == [("file_owned", 7, 20)]


def test_media_events_api_rejects_invalid_cursor_without_reading_events(monkeypatch):
    from flask import Flask
    from app.api.agent_media import agent_get_file_events
    from app.config import Config
    from app.services.agent_runtime.media import file_store

    class FakeService:
        def get(self, _file_id):
            raise AssertionError("invalid cursor must fail before resource lookup")

    monkeypatch.setattr(file_store, "get_agent_file_service", lambda: FakeService())
    monkeypatch.setattr(Config, "DEMO_MODE", True)
    monkeypatch.setattr(Config, "DEMO_STUDENT_ID", "stu_scope")
    app = Flask(__name__)
    with app.test_request_context("/api/agent/files/file_owned/events?after=nope"):
        response, status = agent_get_file_events("file_owned")
    assert status == 400
    assert response.get_json()["code"] == "PARAM_INVALID"


def test_media_events_api_route_is_registered():
    from app import create_app

    app = create_app()
    assert "/api/agent/files/<file_id>/events" in {
        str(rule) for rule in app.url_map.iter_rules()
    }
