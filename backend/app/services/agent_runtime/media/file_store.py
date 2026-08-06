"""
Agent Files — Claude Files / Qoder Files style.

Upload HTTP = bytes + file_id + status=ingesting (fast).
Ingest worker (fork) = extract artifacts → ready|failed.
Never run MinerU on the request thread.
"""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from ....utils.logger import get_logger
from .ingest import TEXT_EXTS, ingest_file
from .schema import ensure_media_schema
from .types import ALLOWED_UPLOAD_EXT, FileStatus, MAX_UPLOAD_BYTES

logger = get_logger("cpa-agent.agent_runtime.media.files")


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha_index_path(sha: str) -> Path:
    d = _upload_root() / "by_sha"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{sha}.json"


def _ext_name(filename: str) -> str:
    return Path(filename or "").suffix.lstrip(".").lower()

_mem_files: Dict[str, Dict[str, Any]] = {}
_mem_artifacts: Dict[str, List[Dict[str, Any]]] = {}
_mem_lock = threading.Lock()
_schema_tried = False


def _upload_root() -> Path:
    try:
        from ....config import Config
        root = Path(Config.UPLOAD_FOLDER) / "agent_files"
    except Exception:
        root = Path(__file__).resolve().parents[4] / "uploads" / "agent_files"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _meta_path(file_id: str) -> Path:
    return _upload_root() / f"{file_id}.meta.json"


def _artifacts_path(file_id: str) -> Path:
    return _upload_root() / f"{file_id}.artifacts.json"


class AgentFileService:
    def __init__(self, mysql_client=None):
        self._mysql = mysql_client

    @property
    def mysql(self):
        if self._mysql is None:
            from ...db import get_mysql_client
            self._mysql = get_mysql_client()
        return self._mysql

    def _ensure_schema_once(self) -> None:
        global _schema_tried
        if _schema_tried:
            return
        if ensure_media_schema(self.mysql):
            _schema_tried = True

    def upload(
        self,
        *,
        file_storage,
        student_id: Optional[str],
        session_id: Optional[str] = None,
        purpose: str = "chat_attachment",
        filename: Optional[str] = None,
    ) -> Dict[str, Any]:
        self._ensure_schema_once()

        name = filename or getattr(file_storage, "filename", None) or "upload.bin"
        # Keep original basename; strip path traversal only
        name = Path(name).name or "upload.bin"
        ext = Path(name).suffix.lstrip(".").lower()
        if ext and ext not in ALLOWED_UPLOAD_EXT:
            raise ValueError(f"不支持的文件类型: .{ext}")

        file_id = f"file_{uuid.uuid4().hex}"
        dest = _upload_root() / f"{file_id}{Path(name).suffix or ''}"

        # Phase A — persist bytes (Claude: upload succeeds independently of processing)
        try:
            file_storage.save(str(dest))
        except Exception as e:
            raise ValueError(f"写入存储失败: {e}") from e

        size = dest.stat().st_size
        if size <= 0:
            try:
                dest.unlink(missing_ok=True)
            except Exception:
                pass
            raise ValueError("上传文件为空")
        if size > MAX_UPLOAD_BYTES:
            try:
                dest.unlink(missing_ok=True)
            except Exception:
                pass
            raise ValueError(f"文件过大（上限 {MAX_UPLOAD_BYTES // (1024 * 1024)}MB）")

        content_sha = ""
        try:
            content_sha = _sha256_file(str(dest))
        except Exception as e:
            logger.debug("sha256 skipped: %s", e)

        mime = getattr(file_storage, "mimetype", None) or "application/octet-stream"
        now = datetime.now().isoformat(sep=" ")
        record = {
            "file_id": file_id,
            "student_id": student_id,
            "session_id": session_id,
            "filename": name,
            "mime_type": mime,
            "size_bytes": size,
            "storage_path": str(dest),
            "status": FileStatus.INGESTING.value,
            "purpose": purpose,
            "downloadable": False,
            "error": None,
            "ingest_meta": {},
            "content_sha256": content_sha,
            "created_at": now,
            "updated_at": now,
        }
        self._persist_file(record)

        # Phase B — ingest off the HTTP thread (Claude: bytes first, process second)
        if self._ingest_inline(ext):
            self.complete_ingest(file_id)
            row = self._load_file(file_id) or record
            return self.public_meta(row)

        try:
            from ..fork_jobs import enqueue as fork_enqueue

            fork_enqueue(
                session_id or "",
                "media_ingest",
                {"file_id": file_id, "purpose": purpose},
            )
        except Exception as e:
            logger.warning("enqueue media_ingest failed, inline fallback: %s", e)
            self.complete_ingest(file_id)
            row = self._load_file(file_id) or record
            return self.public_meta(row)

        return self.public_meta(record)

    @staticmethod
    def _ingest_inline(ext: str) -> bool:
        """Tiny text files are cheap; PDF/DOCX always async (except test inline mode)."""
        try:
            from ..fork_jobs import _run_inline_for_tests
            if _run_inline_for_tests:
                return True
        except Exception:
            pass
        return (ext or "").lower() in TEXT_EXTS

    def complete_ingest(self, file_id: str) -> Optional[Dict[str, Any]]:
        """Worker entry: extract artifacts and flip ready|failed. Idempotent."""
        record = self._load_file(file_id)
        if not record:
            logger.warning("complete_ingest: missing file_id=%s", file_id)
            return None
        if record.get("status") in (FileStatus.READY.value, FileStatus.FAILED.value):
            return self.public_meta(record)

        path = record.get("storage_path") or ""
        name = record.get("filename") or "upload.bin"
        purpose = record.get("purpose") or "chat_attachment"

        # Same bytes already parsed → reuse (scanned resumes often re-uploaded)
        reused = self._try_reuse_by_sha(record)
        if reused:
            return reused

        return self._run_ingest(record, purpose=purpose)

    def upgrade_thin_ingest(self, file_id: str) -> Optional[Dict[str, Any]]:
        """
        Re-run heavy extract when an already-ready file only has a thin head slice.
        Used by Ask materialize so we don't depend on the model to 're-upload'.
        """
        record = self._load_file(file_id)
        if not record:
            return None
        path = record.get("storage_path") or ""
        name = record.get("filename") or "upload.bin"
        full = self.get_artifact(file_id, "full_text") or ""
        from .ingest import light_extract_too_thin

        if not light_extract_too_thin(full, path=str(path), ext=_ext_name(name)):
            return self.public_meta(record)
        logger.info(
            "upgrade_thin_ingest file_id=%s chars=%s — forcing resume_parse",
            file_id,
            len(full),
        )
        record["status"] = FileStatus.INGESTING.value
        record["error"] = None
        self._persist_file(record)
        return self._run_ingest(record, purpose="resume_parse", skip_sha_reuse=True)

    def _run_ingest(
        self,
        record: Dict[str, Any],
        *,
        purpose: str,
        skip_sha_reuse: bool = False,
    ) -> Optional[Dict[str, Any]]:
        file_id = record["file_id"]
        path = record.get("storage_path") or ""
        name = record.get("filename") or "upload.bin"
        if not skip_sha_reuse:
            reused = self._try_reuse_by_sha(record)
            if reused:
                return reused
        try:
            artifacts, meta, ok = ingest_file(str(path), name, purpose=purpose)
            record["ingest_meta"] = meta
            if ok:
                record["status"] = FileStatus.READY.value
                record["error"] = None
            else:
                record["status"] = FileStatus.FAILED.value
                record["error"] = (meta.get("error") or "解析失败")[:500]
            record["updated_at"] = datetime.now().isoformat(sep=" ")
            self._persist_file(record)
            self._persist_artifacts(file_id, artifacts)
            if ok:
                self._index_sha(record)
        except Exception as e:
            logger.exception("ingest failed for %s", file_id)
            record["status"] = FileStatus.FAILED.value
            record["error"] = str(e)[:500]
            record["updated_at"] = datetime.now().isoformat(sep=" ")
            self._persist_file(record)
        return self.public_meta(record)

    def _try_reuse_by_sha(self, record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        sha = (record.get("content_sha256") or "").strip()
        path = record.get("storage_path") or ""
        if not sha and path:
            try:
                sha = _sha256_file(str(path))
                record["content_sha256"] = sha
            except Exception:
                return None
        if not sha:
            return None
        idx = _sha_index_path(sha)
        if not idx.is_file():
            return None
        try:
            donor_id = json.loads(idx.read_text(encoding="utf-8")).get("file_id")
        except Exception:
            return None
        if not donor_id or donor_id == record.get("file_id"):
            return None
        donor = self._load_file(str(donor_id))
        if not donor or donor.get("status") != FileStatus.READY.value:
            return None
        if (donor.get("student_id") or "") != (record.get("student_id") or ""):
            logger.info(
                "ingest sha reuse skipped across owners sha=%s file_id=%s donor=%s",
                sha[:12],
                record.get("file_id"),
                donor_id,
            )
            return None
        arts = self._load_artifacts(str(donor_id))
        if not arts:
            return None
        record["status"] = FileStatus.READY.value
        record["error"] = None
        record["ingest_meta"] = {
            **(donor.get("ingest_meta") or {}),
            "engine": (donor.get("ingest_meta") or {}).get("engine") or "sha_reuse",
            "reused_from": donor_id,
        }
        record["updated_at"] = datetime.now().isoformat(sep=" ")
        self._persist_file(record)
        self._persist_artifacts(record["file_id"], arts)
        logger.info(
            "ingest reuse sha=%s file_id=%s ← %s",
            sha[:12],
            record["file_id"],
            donor_id,
        )
        return self.public_meta(record)

    def _index_sha(self, record: Dict[str, Any]) -> None:
        sha = (record.get("content_sha256") or "").strip()
        if not sha:
            return
        try:
            _sha_index_path(sha).write_text(
                json.dumps({"file_id": record["file_id"]}, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            logger.debug("sha index write skipped: %s", e)

    def get(self, file_id: str) -> Optional[Dict[str, Any]]:
        row = self._load_file(file_id)
        return self.public_meta(row) if row else None

    def get_artifact(
        self,
        file_id: str,
        kind: str = "excerpt",
        *,
        ordinal: int = 0,
    ) -> Optional[str]:
        arts = self._load_artifacts(file_id)
        for a in arts:
            if a.get("kind") == kind and int(a.get("ordinal", 0)) == ordinal:
                return a.get("body_text")
        for a in arts:
            if a.get("kind") == kind:
                return a.get("body_text")
        return None

    def list_artifacts(self, file_id: str) -> List[Dict[str, Any]]:
        return self._load_artifacts(file_id)

    def read_for_model(self, file_id: str, *, max_chars: int = 4000) -> str:
        """
        Legacy helper — prefer document_access.read_window / pack_for_prompt.
        Still truncates; callers must not treat return value as complete.
        """
        from .document_access import format_window_for_model, read_window

        win = read_window(file_id, offset=0, max_chars=max_chars)
        return format_window_for_model(win)

    def public_meta(self, row: Dict[str, Any]) -> Dict[str, Any]:
        excerpt = None
        if row.get("status") in (FileStatus.READY.value, FileStatus.FAILED.value):
            excerpt = self.get_artifact(row["file_id"], "excerpt")
        return {
            "file_id": row["file_id"],
            "type": "file",
            "filename": row.get("filename"),
            "mime_type": row.get("mime_type"),
            "size_bytes": row.get("size_bytes"),
            "status": row.get("status"),
            "purpose": row.get("purpose"),
            "downloadable": bool(row.get("downloadable")),
            "student_id": row.get("student_id"),
            "session_id": row.get("session_id"),
            "excerpt": excerpt,
            "error": row.get("error"),
            "ingest_engine": (row.get("ingest_meta") or {}).get("engine"),
            "created_at": row.get("created_at"),
        }

    # ── persistence: disk catalog primary, MySQL best-effort ─────

    def _persist_file(self, record: Dict[str, Any]) -> None:
        with _mem_lock:
            _mem_files[record["file_id"]] = dict(record)
        # Disk catalog (source of truth)
        try:
            _meta_path(record["file_id"]).write_text(
                json.dumps(record, ensure_ascii=False, indent=0),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("disk catalog write failed: %s", e)

        try:
            self.mysql.execute(
                """
                INSERT INTO agent_file
                (file_id, student_id, session_id, filename, mime_type, size_bytes,
                 storage_path, status, purpose, downloadable, error, ingest_meta_json,
                 created_at, updated_at)
                VALUES
                (:file_id, :student_id, :session_id, :filename, :mime_type, :size_bytes,
                 :storage_path, :status, :purpose, :downloadable, :error, :ingest_meta_json,
                 :created_at, :updated_at)
                ON DUPLICATE KEY UPDATE
                  status=VALUES(status), error=VALUES(error),
                  ingest_meta_json=VALUES(ingest_meta_json), updated_at=VALUES(updated_at)
                """,
                {
                    **{k: record.get(k) for k in (
                        "file_id", "student_id", "session_id", "filename", "mime_type",
                        "size_bytes", "storage_path", "status", "purpose", "error",
                        "created_at", "updated_at",
                    )},
                    "downloadable": 1 if record.get("downloadable") else 0,
                    "ingest_meta_json": json.dumps(
                        record.get("ingest_meta") or {}, ensure_ascii=False,
                    ),
                },
                commit=True,
            )
        except Exception as e:
            logger.info("agent_file mysql persist skipped: %s", e)

    def _persist_artifacts(self, file_id: str, artifacts: List[Dict[str, Any]]) -> None:
        with _mem_lock:
            _mem_artifacts[file_id] = list(artifacts)
        try:
            _artifacts_path(file_id).write_text(
                json.dumps(artifacts, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("disk artifacts write failed: %s", e)

        try:
            for a in artifacts:
                aid = f"art_{uuid.uuid4().hex[:12]}"
                self.mysql.execute(
                    """
                    INSERT INTO agent_file_artifact
                    (artifact_id, file_id, kind, ordinal, body_text, meta_json, created_at)
                    VALUES
                    (:artifact_id, :file_id, :kind, :ordinal, :body_text, :meta_json, :now)
                    """,
                    {
                        "artifact_id": aid,
                        "file_id": file_id,
                        "kind": a.get("kind"),
                        "ordinal": int(a.get("ordinal") or 0),
                        "body_text": a.get("body_text") or "",
                        "meta_json": json.dumps(a.get("meta") or {}, ensure_ascii=False),
                        "now": datetime.now().isoformat(sep=" "),
                    },
                    commit=True,
                )
        except Exception as e:
            logger.info("artifact mysql persist skipped: %s", e)

    def _load_file(self, file_id: str) -> Optional[Dict[str, Any]]:
        with _mem_lock:
            if file_id in _mem_files:
                return dict(_mem_files[file_id])
        # Disk catalog
        mp = _meta_path(file_id)
        if mp.is_file():
            try:
                d = json.loads(mp.read_text(encoding="utf-8"))
                with _mem_lock:
                    _mem_files[file_id] = d
                return dict(d)
            except Exception as e:
                logger.warning("disk catalog read failed: %s", e)
        try:
            row = self.mysql.fetch_one(
                "SELECT * FROM agent_file WHERE file_id = :file_id LIMIT 1",
                {"file_id": file_id},
            )
            if not row:
                return None
            d = dict(row)
            raw = d.pop("ingest_meta_json", None)
            try:
                d["ingest_meta"] = json.loads(raw) if raw else {}
            except Exception:
                d["ingest_meta"] = {}
            with _mem_lock:
                _mem_files[file_id] = d
            return d
        except Exception as e:
            logger.info("load file failed: %s", e)
            return None

    def _load_artifacts(self, file_id: str) -> List[Dict[str, Any]]:
        with _mem_lock:
            if file_id in _mem_artifacts:
                return list(_mem_artifacts[file_id])
        ap = _artifacts_path(file_id)
        if ap.is_file():
            try:
                out = json.loads(ap.read_text(encoding="utf-8"))
                if isinstance(out, list):
                    with _mem_lock:
                        _mem_artifacts[file_id] = out
                    return list(out)
            except Exception:
                pass
        try:
            rows = self.mysql.fetch_all(
                """
                SELECT kind, ordinal, body_text, meta_json
                FROM agent_file_artifact WHERE file_id = :file_id
                ORDER BY kind, ordinal
                """,
                {"file_id": file_id},
            ) or []
            out = []
            for r in rows:
                meta = {}
                try:
                    meta = json.loads(r.get("meta_json") or "{}")
                except Exception:
                    pass
                out.append({
                    "kind": r.get("kind"),
                    "ordinal": r.get("ordinal") or 0,
                    "body_text": r.get("body_text") or "",
                    "meta": meta,
                })
            with _mem_lock:
                _mem_artifacts[file_id] = out
            return out
        except Exception:
            return []


_svc: Optional[AgentFileService] = None
_svc_lock = threading.Lock()


def get_agent_file_service() -> AgentFileService:
    global _svc
    if _svc is None:
        with _svc_lock:
            if _svc is None:
                _svc = AgentFileService()
    return _svc


def _handle_media_ingest(job) -> None:
    fid = (job.payload or {}).get("file_id")
    if not fid:
        return
    logger.info("media_ingest worker start file_id=%s", fid)
    out = get_agent_file_service().complete_ingest(str(fid))
    logger.info(
        "media_ingest worker done file_id=%s status=%s engine=%s",
        fid,
        (out or {}).get("status"),
        (out or {}).get("ingest_engine"),
    )


try:
    from ..fork_jobs import register_handler

    register_handler("media_ingest", _handle_media_ingest)
except Exception as e:  # pragma: no cover
    logger.warning("register media_ingest handler skipped: %s", e)
