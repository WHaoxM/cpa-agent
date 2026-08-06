"""Document Access Layer — progressive disclosure beyond blind chunking."""

from types import SimpleNamespace

from app.services.agent_runtime.fork_jobs import set_inline_for_tests
from app.services.agent_runtime.media.document_access import (
    assert_document_access,
    document_catalog,
    find_windows,
    format_window_for_model,
    pack_for_prompt,
    read_section,
    read_window,
)
from app.services.agent_runtime.media.session_resources import mount_resources
from app.services.agent_runtime.media.file_store import AgentFileService
from app.services.agent_runtime.media.types import CHARS_INLINE_COMPLETE


class _MemFile:
    def __init__(self, name: str, data: bytes, mimetype: str = "text/plain"):
        self.filename = name
        self.mimetype = mimetype
        self._data = data

    def save(self, path: str):
        with open(path, "wb") as f:
            f.write(self._data)


def _svc(tmp_path, monkeypatch):
    set_inline_for_tests(True)
    svc = AgentFileService(mysql_client=SimpleNamespace())
    monkeypatch.setattr(
        "app.services.agent_runtime.media.file_store._upload_root",
        lambda: tmp_path,
    )
    monkeypatch.setattr(
        "app.services.agent_runtime.media.file_store.ensure_media_schema",
        lambda *_a, **_k: False,
    )

    class Boom:
        def execute(self, *a, **k):
            raise RuntimeError("no mysql")

        def fetch_one(self, *a, **k):
            return None

        def fetch_all(self, *a, **k):
            return []

    svc._mysql = Boom()
    return svc


def _force_full_text(svc: AgentFileService, file_id: str, text: str) -> None:
    """Bypass ingest field-policy caps so access-layer tests see raw length."""
    arts = [
        {"kind": "excerpt", "ordinal": 0, "body_text": text[:200], "meta": {}},
        {"kind": "full_text", "ordinal": 0, "body_text": text, "meta": {"chars": len(text)}},
    ]
    svc._persist_artifacts(file_id, arts)
    row = svc._load_file(file_id) or {}
    row["status"] = "ready"
    row["error"] = None
    svc._persist_file(row)


def test_small_doc_pack_is_complete(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    # Same class as production ~3323-char resume: >3000 so old silent truncate would drop the tail
    text = "段落内容ABCDEF。" * 280
    assert 3000 < len(text) < CHARS_INLINE_COMPLETE
    meta = svc.upload(
        file_storage=_MemFile("resume.txt", text.encode("utf-8")),
        student_id="stu_doc",
    )
    _force_full_text(svc, meta["file_id"], text)
    pack, report = pack_for_prompt([meta["file_id"]])
    assert report["all_complete"] is True
    assert "complete=true" in pack
    assert "has_more=false" in pack
    assert text[-40:] in pack


def test_window_has_more_and_pages(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    text = "X" * 10000
    meta = svc.upload(
        file_storage=_MemFile("big.txt", text.encode("utf-8")),
        student_id="stu_doc",
    )
    _force_full_text(svc, meta["file_id"], text)
    w0 = read_window(meta["file_id"], offset=0, max_chars=4000)
    assert w0.has_more is True
    assert w0.complete is False
    assert w0.next_offset == 4000
    w1 = read_window(meta["file_id"], offset=w0.next_offset, max_chars=4000)
    assert w1.offset == 4000
    out = format_window_for_model(w0)
    assert "has_more=true" in out
    assert "继续" in out or "next_offset" in out


def test_pack_oversized_not_pretend_complete(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    text = "Y" * (CHARS_INLINE_COMPLETE + 500)
    meta = svc.upload(
        file_storage=_MemFile("huge.txt", text.encode("utf-8")),
        student_id="stu_doc",
    )
    _force_full_text(svc, meta["file_id"], text)
    pack, report = pack_for_prompt([meta["file_id"]])
    assert report["all_complete"] is False
    assert "has_more=true" in pack or "分页" in pack


def test_excerpt_fallback_never_complete(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    text = "姓名张三\n教育背景\n项目经验\n" + ("正文" * 80)
    meta = svc.upload(
        file_storage=_MemFile("partial.txt", text.encode("utf-8")),
        student_id="stu_doc",
    )
    # Only excerpt artifact — mimics failed/incomplete ingest
    svc._persist_artifacts(
        meta["file_id"],
        [{"kind": "excerpt", "ordinal": 0, "body_text": text[:400], "meta": {}}],
    )
    row = svc._load_file(meta["file_id"]) or {}
    row["status"] = "ready"
    svc._persist_file(row)

    win = read_window(meta["file_id"], offset=0, max_chars=8000)
    assert win.complete is False
    pack, report = pack_for_prompt([meta["file_id"]])
    assert report["all_complete"] is False
    assert "complete=true" not in pack
    assert "excerpt" in pack.lower() or "勿当全文" in pack


def test_attachment_read_egress_preserves_page(monkeypatch):
    """Generic 4k tool egress must not silently head-slice Document Access windows."""
    from app.services.agent_runtime.media.types import CHARS_READ_PAGE_MAX
    from app.services.agent_runtime.tool_result_channels import build_model_view

    # Header + body just under one-page budget — must survive egress intact
    body = ("教育背景与项目经验段落。" * 400)  # ~4800 chars; old egress capped at 4000
    window = (
        "文件 resume.pdf (file_id=f1)\n"
        f"total_chars={len(body)} offset=0 returned={len(body)} "
        "has_more=false complete=true\n"
        "【完整性：本段为附件全文，可据此作答】\n"
        f"---\n{body}"
    )
    assert len(window) > 4000  # would have been cut by old egress
    assert len(window) < CHARS_READ_PAGE_MAX + 600
    out = build_model_view("attachment_read", window)
    assert "complete=true" in out
    assert body[-30:] in out
    assert not out.endswith("…")

    # Oversized payload: if egress must cut, flags must be rewritten
    huge = "Z" * (CHARS_READ_PAGE_MAX + 2000)
    huge_win = (
        "文件 big.pdf (file_id=f2)\n"
        f"total_chars={len(huge)} offset=0 returned={len(huge)} "
        "has_more=false complete=true\n"
        "【完整性：本段为附件全文，可据此作答】\n"
        f"---\n{huge}"
    )
    cut = build_model_view("attachment_read", huge_win)
    assert "complete=false" in cut
    assert "has_more=true" in cut
    # Generic tools still use 4k
    other = build_model_view("db_query", "X" * 5000)
    assert len(other) <= 4001
    assert other.endswith("…")


def _resume_text() -> str:
    return (
        "钟梓珉\n2006年11月\n\n"
        "教育背景\n某大学 计算机科学 本科 GPA 3.8\n\n"
        "项目经验\nGraphRAG Agent 平台：负责召回与反馈闭环\n"
        + ("项目细节描述。" * 40)
        + "\n\n实习经历\n某AI公司 Agent 工程实习生\n"
        + ("实习工作内容。" * 30)
        + "\n\n专业技能\nPython RAG Prompt Context\n"
    )


def test_catalog_has_section_offsets(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    text = _resume_text()
    meta = svc.upload(
        file_storage=_MemFile("resume.txt", text.encode("utf-8")),
        student_id="stu_doc",
    )
    _force_full_text(svc, meta["file_id"], text)
    cat = document_catalog(meta["file_id"])
    sections = cat.get("sections") or []
    assert len(sections) >= 3
    titles = " ".join(str(s.get("title") or "") for s in sections)
    assert "项目" in titles or "教育" in titles
    for s in sections:
        assert "offset" in s and "end_offset" in s
        assert int(s["end_offset"]) >= int(s["offset"])


def test_find_jumps_to_projects_never_complete(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    text = _resume_text()
    meta = svc.upload(
        file_storage=_MemFile("resume.txt", text.encode("utf-8")),
        student_id="stu_doc",
    )
    _force_full_text(svc, meta["file_id"], text)
    hits = find_windows(meta["file_id"], query="最近做的项目和 GraphRAG", top_k=2)
    assert hits
    joined = "\n".join(h.get("text") or "" for h in hits)
    assert "GraphRAG" in joined or "项目" in joined
    for h in hits:
        assert h.get("complete") is False
        assert h.get("coverage") == "partial"


def test_section_read_partial(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    text = _resume_text()
    meta = svc.upload(
        file_storage=_MemFile("resume.txt", text.encode("utf-8")),
        student_id="stu_doc",
    )
    _force_full_text(svc, meta["file_id"], text)
    cat = document_catalog(meta["file_id"])
    proj = next(
        (s for s in cat["sections"] if "项目" in str(s.get("title") or "")),
        cat["sections"][1],
    )
    win = read_section(meta["file_id"], section_id=proj["id"])
    assert win.complete is False
    assert win.coverage == "section"
    assert "项目" in win.text or "GraphRAG" in win.text


def test_ask_budget_packs_typical_resume_complete(tmp_path, monkeypatch):
    """DeepSeek-style: mid-size resume must complete-pack under 48k budget."""
    svc = _svc(tmp_path, monkeypatch)
    # ~12k chars: formerly above old 16k? No — prove 12k still packs under 48k
    text = "教育背景\n\n项目经验\n" + ("详细项目与实习描述内容。" * 1200)
    assert 10000 < len(text) < CHARS_INLINE_COMPLETE
    meta = svc.upload(
        file_storage=_MemFile("mid.txt", text.encode("utf-8")),
        student_id="stu_doc",
    )
    _force_full_text(svc, meta["file_id"], text)
    pack, report = pack_for_prompt([meta["file_id"]])
    assert report["all_complete"] is True
    assert "complete=true" in pack


def test_edu_at_tail_must_survive_complete_pack(tmp_path, monkeypatch):
    """Zhong-style layout: 教育经历 after ~3150 chars — never drop the tail."""
    from app.services.agent_runtime.media.document_access import materialize_for_ask

    svc = _svc(tmp_path, monkeypatch)
    head = (
        "钟梓珉\n2006/11\n专业技能\nRAG GraphRAG Agent\n"
        + ("项目与实习长描述。" * 400)
    )
    # Ensure education sits past the old silent ~3000 cut
    assert len(head) > 3000
    text = head + "\n教育经历\n四川轻化工大学\n计算机科学与技术·本科\n一等奖学金\n"
    assert text.find("教育经历") > 3000
    meta = svc.upload(
        file_storage=_MemFile("zhong.txt", text.encode("utf-8")),
        student_id="stu_doc",
    )
    _force_full_text(svc, meta["file_id"], text)
    pack, report = materialize_for_ask([meta["file_id"]], query="分析简历")
    assert report["all_complete"] is True
    assert "complete=true" in pack
    assert "教育经历" in pack
    assert "四川轻化工大学" in pack


def test_document_access_requires_owner_and_session_mount(tmp_path, monkeypatch):
    svc = _svc(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "app.services.agent_runtime.media.session_resources._mount_root",
        lambda: tmp_path / "mounts",
    )
    text = "scoped document body"
    meta = svc.upload(
        file_storage=_MemFile("scoped.txt", text.encode("utf-8")),
        student_id="stu_doc",
    )

    try:
        assert_document_access(
            meta["file_id"],
            student_id="stu_doc",
            session_id="sess_doc",
        )
        assert False, "unmounted file should be denied"
    except PermissionError as e:
        assert "mounted" in str(e)

    mount_resources(
        "sess_doc",
        [{"file_id": meta["file_id"]}],
        student_id="stu_doc",
    )
    assert_document_access(
        meta["file_id"],
        student_id="stu_doc",
        session_id="sess_doc",
    )

    try:
        assert_document_access(
            meta["file_id"],
            student_id="other_stu",
            session_id="sess_doc",
        )
        assert False, "cross-owner file should be denied"
    except PermissionError as e:
        assert "student_id" in str(e)
