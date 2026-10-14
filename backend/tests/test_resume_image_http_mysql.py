"""Owner-scoped socket HTTP image upload through durable L0 and Trace.

The OCR and structure model are deliberately substituted; image-to-PDF,
MySQL task state, account auth, route registration, and trace are real.
"""

from io import BytesIO
import json
import os
import threading
import uuid
from pathlib import Path

import pytest
import requests
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import text
from werkzeug.serving import make_server


pytestmark = pytest.mark.skipif(
    os.getenv("CPA_TEST_LIVE_MYSQL") != "1",
    reason="requires Docker MySQL and Redis",
)


def _resume_image(image_format="PNG"):
    image = Image.new("RGB", (1100, 1500), "white")
    draw = ImageDraw.Draw(image)
    font_path = Path("C:/Windows/Fonts/msyh.ttc")
    font = ImageFont.truetype(str(font_path), 46) if font_path.is_file() else ImageFont.load_default()
    for index, line in enumerate((
        "张同学 合成测试简历", "教育经历 测试大学 计算机科学", "项目经历 数据分析系统",
        "Python SQL Vue Docker", "2024 2025 Internship Project",
    )):
        draw.text((65, 100 + index * 160), line, fill="black", font=font)
    stream = BytesIO()
    image.save(stream, format=image_format)
    return stream.getvalue()


def test_registered_image_upload_reaches_ocr_candidate_or_explicit_error(monkeypatch, tmp_path):
    from app import create_app
    from app.config import Config
    from app.pipeline.entity_extractor import l0_full_parser, pdf_sidecar
    from app.pipeline.entity_extractor.pdf_engines import EngineState, EngineStatus
    from app.services import pipeline_resume_jobs as jobs
    from app.services.db.mysql_client import get_mysql_client
    from app.services.resume_agent_team import EntityAgent, StructureAgent
    import pytesseract

    nonce = uuid.uuid4().hex[:12]
    username = f"image_student_{nonce}"
    other_username = f"image_other_{nonce}"
    password = "strong-image-test-password-42"
    monkeypatch.setattr(Config, "UPLOAD_FOLDER", str(tmp_path))
    config = type("ResumeImageHttpConfig", (Config,), {
        "SECRET_KEY": "only-for-image-test-a-long-randomly-shaped-secret-12345",
        "DEMO_MODE": False,
        "CPA_AUTH_COOKIE_SECURE": False,
        "UPLOAD_FOLDER": str(tmp_path),
        "RATELIMIT_KEY_PREFIX": f"resume-image-test-{nonce}",
    })
    isolated_queue = f"image-audit-{nonce}"
    monkeypatch.setattr(jobs, "PIPELINE_QUEUE", isolated_queue)
    app = create_app(config)
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    session, other = requests.Session(), requests.Session()
    db = get_mysql_client()
    task_ids = []
    saved_paths = []
    try:
        csrf = session.get(f"{base}/api/auth/csrf", timeout=5).json()["csrfToken"]
        credentials = {"username": username, "password": password}
        registered = session.post(f"{base}/api/auth/register", json=credentials,
                                  headers={"X-CSRFToken": csrf}, timeout=5)
        assert registered.status_code == 201, registered.text
        student_id = registered.json()["user"]["id"]
        assert session.post(f"{base}/api/auth/login", json=credentials,
                            headers={"X-CSRFToken": csrf}, timeout=5).status_code == 200
        initial_profile = db.fetch_one("SELECT profile_json FROM student_profile WHERE student_id=:sid",
                                       {"sid": student_id})

        def upload(filename="fictional-resume.png", payload=None):
            response = session.post(
                f"{base}/api/profile/student/upload?async=1",
                files={"file": (filename, payload if payload is not None else _resume_image(),
                                "image/tiff" if filename.endswith((".tif", ".tiff")) else "image/png")},
                headers={"X-CSRFToken": csrf}, timeout=10,
            )
            assert response.status_code == 202, response.text
            task_id = response.json()["data"]["task_id"]
            task_ids.append(task_id)
            task = db.fetch_one("SELECT params_json FROM pipeline_task WHERE task_id=:tid", {"tid": task_id})
            saved_paths.append(json.loads(task["params_json"])["file_path"])
            return task_id

        def fake_ocr(pdf_path):
            import pymupdf

            with pymupdf.open(pdf_path) as pdf:
                assert len(pdf) == 1 and pdf[0].get_images()
            return {"success": True, "text": "学校 专业 Python 项目",
                    "blocks": [{"text": "学校 专业 Python 项目", "page": 0}],
                    "engine": "mineru", "metadata": {"pages": 1}}

        real_ocr = os.getenv("CPA_TEST_REAL_IMAGE_OCR") == "1"
        if real_ocr:
            from app.pipeline.entity_extractor.pdf_engines import reset_engine_states

            monkeypatch.setenv("CPA_L0_PARSE_SIDECAR_URL", "http://127.0.0.1:9380")
            reset_engine_states()
        else:
            monkeypatch.setattr(l0_full_parser, "get_sidecar_state", lambda: EngineState(
                "sidecar", EngineStatus.READY, "test OCR"))
            monkeypatch.setattr(pdf_sidecar, "parse_pdf_via_sidecar", fake_ocr)
        profile = {"school": "学校", "major": "专业", "skills": ["Python"],
                   "projects": [{"name": "测试项目"}]}
        monkeypatch.setattr(StructureAgent, "run", lambda self, document: (
            {"success": True, "student_profile": profile, "structure_validation": {"ok": True}}, object()))
        monkeypatch.setattr(EntityAgent, "run", lambda self, candidate, llm, document: candidate)

        successful_task = upload()
        result = jobs.execute_resume_intent(successful_task, mysql=db)
        assert result["status"] == "needs_input"
        assert result["source_evidence"]["engine"] == "sidecar"
        assert result["source_evidence"]["page_count"] == 1
        if real_ocr:
            assert "python" in " ".join(
                block["text"] for block in result["source_evidence"]["blocks"]).lower()
        trace_response = session.get(f"{base}/api/pipeline/trace/{successful_task}", timeout=5)
        assert trace_response.status_code == 200, trace_response.text
        trace = trace_response.json()["data"]
        assert trace["task"]["progress"] == 100
        assert trace["task"]["current_attempt"] == 1
        assert [(event["stage"], event["progress"]) for event in trace["events"]
                if event["stage"] in {"source_extracted", "candidate_saved"}] == [
            ("source_extracted", 25), ("candidate_saved", 100)]
        extracted = next(event for event in trace["events"] if event["stage"] == "source_extracted")
        assert extracted["facts"]["engine"] == "sidecar"

        # Both names advertise image/tiff and contain exactly the same bytes.
        # The independent HTTP route, owner-bound MySQL intent, parser, and
        # candidate/Trace must all agree rather than accepting only .tiff.
        tiff_bytes = _resume_image("TIFF")
        tiff_results = []
        for extension in ("tiff", "tif"):
            tiff_task = upload(f"fictional-resume.{extension}", tiff_bytes)
            tiff_result = jobs.execute_resume_intent(tiff_task, mysql=db)
            assert tiff_result["status"] == "needs_input"
            assert tiff_result["source_evidence"]["engine"] == "sidecar"
            assert tiff_result["source_evidence"]["page_count"] == 1
            tiff_results.append(tiff_result)
            candidate = session.get(f"{base}/api/profile/student/candidate/{tiff_task}", timeout=5)
            assert candidate.status_code == 200, candidate.text
            assert candidate.json()["data"]["source_evidence"]["page_count"] == 1
            tiff_trace = session.get(f"{base}/api/pipeline/trace/{tiff_task}", timeout=5)
            assert tiff_trace.status_code == 200, tiff_trace.text
            assert tiff_trace.json()["data"]["task"]["progress"] == 100
        assert (tiff_results[0]["source_evidence"]["blocks"] ==
                tiff_results[1]["source_evidence"]["blocks"])

        legacy = session.post(
            f"{base}/api/pipeline/ext/parse/upload/resume",
            files={"file": ("fictional-resume.tif", tiff_bytes, "image/tiff")},
            headers={"X-CSRFToken": csrf}, timeout=10,
        )
        assert legacy.status_code == 200, legacy.text
        assert legacy.json()["data"]["metadata"]["source_format"] == "tif"

        other_csrf = other.get(f"{base}/api/auth/csrf", timeout=5).json()["csrfToken"]
        other_credentials = {"username": other_username, "password": password}
        assert other.post(f"{base}/api/auth/register", json=other_credentials,
                          headers={"X-CSRFToken": other_csrf}, timeout=5).status_code == 201
        assert other.post(f"{base}/api/auth/login", json=other_credentials,
                          headers={"X-CSRFToken": other_csrf}, timeout=5).status_code == 200
        denied = other.get(f"{base}/api/pipeline/trace/{successful_task}", timeout=5)
        assert denied.status_code == 404, denied.text
        denied_tiff = other.get(f"{base}/api/pipeline/trace/{task_ids[-1]}", timeout=5)
        assert denied_tiff.status_code == 404, denied_tiff.text

        # Same owner, same image, but no actual OCR capability: no candidate or
        # fake checkpoint, and the status route preserves a retryable reason.
        monkeypatch.setattr(l0_full_parser, "get_sidecar_state", lambda: EngineState(
            "sidecar", EngineStatus.DISABLED, "not configured"))
        monkeypatch.setattr(l0_full_parser.L0FullParser, "_check_yolo", lambda self: False)
        monkeypatch.setattr(pytesseract, "image_to_string", lambda *_args, **_kwargs: (
            (_ for _ in ()).throw(pytesseract.TesseractNotFoundError())))
        failed_task = upload()
        assert jobs.execute_resume_intent(failed_task, mysql=db)["status"] == "failed"
        status_response = session.get(f"{base}/api/pipeline/status/{failed_task}", timeout=5)
        assert status_response.status_code == 200, status_response.text
        status = status_response.json()["data"]
        assert (status["status"], status["progress"], status["result"]) == ("failed", 0, None)
        assert "OCR" in status["error"]
        failed_trace = session.get(f"{base}/api/pipeline/trace/{failed_task}", timeout=5).json()["data"]
        assert (failed_trace["task"]["status"], failed_trace["task"]["progress"]) == ("failed", 0)
        assert failed_trace["events"][-1]["stage"] == "failed"
        assert not any(event["stage"] == "source_extracted" for event in failed_trace["events"])
        assert db.fetch_one("SELECT profile_json FROM student_profile WHERE student_id=:sid",
                            {"sid": student_id}) == initial_profile
    finally:
        session.close()
        other.close()
        server.shutdown()
        thread.join(timeout=5)
        with db.engine.begin() as conn:
            for task_id in task_ids:
                for table in ("pipeline_task_trace", "pipeline_resume_dispatch",
                              "pipeline_task_event", "pipeline_task"):
                    conn.execute(text(f"DELETE FROM {table} WHERE task_id=:tid"), {"tid": task_id})
        db.execute_write("DELETE FROM user_account WHERE username IN (:u1,:u2)",
                         {"u1": username, "u2": other_username})
        from app.services.agent_runtime.media.jobs import celery_app
        from redis import Redis

        with celery_app.connection_for_write() as broker:
            broker.default_channel.queue_delete(isolated_queue)
        Redis.from_url(celery_app.conf.broker_url).delete(isolated_queue)
        for path in saved_paths:
            if os.path.isfile(path):
                os.remove(path)
            try:
                os.rmdir(os.path.dirname(path))
            except OSError:
                pass
        if os.getenv("CPA_TEST_REAL_IMAGE_OCR") == "1":
            from app.pipeline.entity_extractor.pdf_engines import reset_engine_states

            reset_engine_states()
