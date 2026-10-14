"""Legacy OLE2 .doc must not be sent to the OOXML-only DOCX parser."""

import os
from pathlib import Path

import pytest

from app.pipeline.entity_extractor.l0_full_parser import L0FullParser
from app.services.agent_runtime.media.ingest import extract_text_from_path
from app.utils import legacy_doc_text


def _ole_doc(path: Path) -> None:
    path.write_bytes(legacy_doc_text.OLE2_MAGIC + b"\0" * 64)


def test_missing_tika_is_recoverable_not_ooxml_package_error(tmp_path, monkeypatch):
    path = tmp_path / "fictional-resume.doc"
    _ole_doc(path)
    monkeypatch.delenv("CPA_L0_DOC_TIKA_JAR", raising=False)

    result = L0FullParser().parse_file(path)

    assert not result.success and not result.text
    assert result.metadata["source_coverage"] == "unverified"
    assert result.metadata["retryable"] is True
    assert "CPA_L0_DOC_TIKA_JAR" in result.error
    assert "Package not found" not in result.error


def test_non_ole_doc_is_rejected_before_java(tmp_path, monkeypatch):
    path = tmp_path / "fake.doc"
    path.write_bytes(b"not an OLE document")
    monkeypatch.setenv("CPA_L0_DOC_TIKA_JAR", str(tmp_path / "missing.jar"))

    result = L0FullParser().parse_file(path)

    assert not result.success
    assert "不是旧版二进制" in result.error


def test_doc_text_extraction_is_explicitly_unverified_and_temp_is_removed(
    tmp_path, monkeypatch,
):
    path = tmp_path / "fictional-resume.doc"
    jar = tmp_path / "tika.jar"
    _ole_doc(path)
    jar.write_bytes(b"test jar placeholder")
    monkeypatch.setenv("CPA_L0_DOC_TIKA_JAR", str(jar))
    monkeypatch.setattr(legacy_doc_text.shutil, "which", lambda _name: "java")
    observed = {}

    class FakeProcess:
        returncode = 0

        def __init__(self, argv, stdout, stderr, env, creationflags):
            assert argv[1] == "-Xmx512m"
            assert argv[-2:] == ["--text", str(path)]
            assert argv[2].startswith("-Djava.io.tmpdir=")
            assert env["TEMP"] == env["TMP"] == env["TMPDIR"]
            assert creationflags == getattr(legacy_doc_text.subprocess, "CREATE_NO_WINDOW", 0)
            observed["temporary"] = Path(env["TEMP"])
            stdout.write("Synthetic University Python".encode("utf-8"))

        def poll(self):
            return self.returncode

    monkeypatch.setattr(legacy_doc_text.subprocess, "Popen", FakeProcess)

    result = L0FullParser().parse_file(path)

    assert result.success
    assert result.text == "Synthetic University Python"
    assert result.engine == "apache-tika-doc-text"
    assert result.metadata["source_coverage"] == "unverified"
    assert "embedded_media" in result.metadata["coverage_reason"]
    assert result.metadata["reading_order"] == "tika_text_projection"
    assert len(result.blocks) == 1
    assert result.blocks[0].page is None
    assert not observed["temporary"].exists()


def test_doc_tika_failure_cannot_report_parsed_text(tmp_path, monkeypatch):
    path = tmp_path / "fictional-resume.doc"
    jar = tmp_path / "tika.jar"
    _ole_doc(path)
    jar.write_bytes(b"test jar placeholder")
    monkeypatch.setenv("CPA_L0_DOC_TIKA_JAR", str(jar))
    monkeypatch.setattr(legacy_doc_text.shutil, "which", lambda _name: "java")

    class FailedProcess:
        returncode = 3

        def __init__(self, argv, stdout, stderr, env, creationflags):
            stdout.write(b"partial text must be discarded")
            stderr.write(b"sensitive parser diagnostics")

        def poll(self):
            return self.returncode

    monkeypatch.setattr(legacy_doc_text.subprocess, "Popen", FailedProcess)

    result = L0FullParser().parse_file(path)

    assert not result.success and result.text == ""
    assert "退出码 3" in result.error
    assert "sensitive" not in result.error


@pytest.mark.parametrize("write_to", ["stdout", "stderr"])
def test_doc_tika_output_limit_kills_process(tmp_path, monkeypatch, write_to):
    path = tmp_path / "fictional-resume.doc"
    jar = tmp_path / "tika.jar"
    _ole_doc(path)
    jar.write_bytes(b"test jar placeholder")
    monkeypatch.setenv("CPA_L0_DOC_TIKA_JAR", str(jar))
    monkeypatch.setattr(legacy_doc_text.shutil, "which", lambda _name: "java")
    monkeypatch.setattr(legacy_doc_text, "MAX_TEXT_BYTES", 8)
    monkeypatch.setattr(legacy_doc_text, "MAX_DIAGNOSTIC_BYTES", 8)
    observed = {}

    class OverflowProcess:
        returncode = None

        def __init__(self, argv, stdout, stderr, env, creationflags):
            observed["process"] = self
            {"stdout": stdout, "stderr": stderr}[write_to].write(b"too many bytes")
            {"stdout": stdout, "stderr": stderr}[write_to].flush()
            self.killed = False

        def poll(self):
            return self.returncode

        def kill(self):
            self.killed = True
            self.returncode = -9

        def wait(self, timeout=None):
            return self.returncode

    monkeypatch.setattr(legacy_doc_text.subprocess, "Popen", OverflowProcess)

    result = L0FullParser().parse_file(path)

    assert not result.success and result.text == ""
    assert "安全上限" in result.error
    assert observed["process"].killed


def test_doc_tika_timeout_kills_process(tmp_path, monkeypatch):
    path = tmp_path / "fictional-resume.doc"
    jar = tmp_path / "tika.jar"
    _ole_doc(path)
    jar.write_bytes(b"test jar placeholder")
    monkeypatch.setenv("CPA_L0_DOC_TIKA_JAR", str(jar))
    monkeypatch.setattr(legacy_doc_text.shutil, "which", lambda _name: "java")
    monkeypatch.setattr(legacy_doc_text, "TIMEOUT_SECONDS", -1)
    observed = {}

    class StuckProcess:
        returncode = None

        def __init__(self, argv, stdout, stderr, env, creationflags):
            observed["process"] = self
            self.killed = False

        def poll(self):
            return self.returncode

        def kill(self):
            self.killed = True
            self.returncode = -9

        def wait(self, timeout=None):
            return self.returncode

    monkeypatch.setattr(legacy_doc_text.subprocess, "Popen", StuckProcess)

    result = L0FullParser().parse_file(path)

    assert not result.success and result.text == ""
    assert "超时" in result.error
    assert observed["process"].killed


def test_doc_chat_ingest_keeps_unverified_coverage(tmp_path, monkeypatch):
    path = tmp_path / "fictional-resume.doc"
    _ole_doc(path)
    monkeypatch.setattr(legacy_doc_text, "extract_legacy_doc_text", lambda _path: (
        "Synthetic University Python", {
            "engine": "apache-tika-doc-text", "source_coverage": "unverified",
            "coverage_reason": "legacy_doc_text_only_embedded_media_and_pages_unverified",
            "retryable": True,
        },
    ))

    text, meta = extract_text_from_path(str(path), path.name, prefer_light=True, allow_heavy=True)

    assert text == "Synthetic University Python"
    assert meta["engine"] == "apache-tika-doc-text"
    assert meta["source_coverage"] == "unverified"
    assert meta["retryable"] is True


@pytest.mark.skipif(
    os.getenv("CPA_TEST_REAL_LEGACY_DOC") != "1",
    reason="requires pinned Apache Tika jar and public Apache POI test file",
)
def test_public_word97_sample_real_tika():
    sample = Path(os.environ["CPA_TEST_LEGACY_DOC_SAMPLE"])
    assert sample.read_bytes().startswith(legacy_doc_text.OLE2_MAGIC)

    result = L0FullParser().parse_file(sample)

    assert result.success, result.error
    assert "Word 97-SR2" in result.text
    assert result.metadata["source_coverage"] == "unverified"
