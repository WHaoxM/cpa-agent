"""
Agent Files — Claude Files / Qoder Files style.

Upload HTTP = bytes + file_id + status=ingesting (fast).
Celery media worker = extract artifacts → ready|failed.
Never run MinerU on the request thread.
"""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from ....utils.logger import get_logger
from .ingest import TEXT_EXTS, ingest_file
from .schema import ensure_media_schema
from .types import ALLOWED_UPLOAD_EXT, FileStatus, MAX_UPLOAD_BYTES

logger = get_logger("cpa-agent.agent_runtime.media.files")

# Legacy process-local records still need a bound. Celery-backed records are
# redelivered by Redis and must not be rewritten as failed merely because the
# Flask process restarted.
JOB_STALE_AFTER = timedelta(hours=1)
MEDIA_EVENT_STATUSES = frozenset({"queued", "ingesting", "ready", "failed"})
MEDIA_EVENT_PHASES = frozenset({"ingest", "upgrade"})
MAX_MEDIA_EVENTS = 500


class MediaEventLogError(RuntimeError):
    """Persisted media events are invalid; replay must fail closed."""


def _validated_events(record: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw = (record.get("ingest_meta") or {}).get("events")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise MediaEventLogError("media event log is not a list")
    events: List[Dict[str, Any]] = []
    previous = 0
    for item in raw:
        if not isinstance(item, dict):
            raise MediaEventLogError("media event entry is not an object")
        try:
            seq = int(item.get("seq"))
            attempt = int(item.get("attempt"))
        except (TypeError, ValueError) as exc:
            raise MediaEventLogError("media event seq/attempt is invalid") from exc
        if seq != previous + 1 or attempt < 1:
            raise MediaEventLogError("media event sequence is not contiguous")
        if item.get("status") not in MEDIA_EVENT_STATUSES:
            raise MediaEventLogError("media event status is invalid")
        if item.get("phase") not in MEDIA_EVENT_PHASES:
            raise MediaEventLogError("media event phase is invalid")
        if not item.get("occurred_at"):
            raise MediaEventLogError("media event timestamp is missing")
        events.append(dict(item))
        previous = seq
    return events


def _append_state_event(
    record: Dict[str, Any],
    *,
    status: str,
    phase: str,
    attempt: int,
    job_id: Optional[str] = None,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    """Append one authoritative state transition inside the atomic file record."""
    if status not in MEDIA_EVENT_STATUSES or phase not in MEDIA_EVENT_PHASES:
        raise ValueError(f"invalid media event transition: {phase}/{status}")
    attempt = max(1, int(attempt))
    meta = dict(record.get("ingest_meta") or {})
    events = _validated_events(record)
    # Celery is at-least-once. A redelivery of the same logical transition must
    # not create a second public event.
    if any(
        event.get("phase") == phase
        and event.get("status") == status
        and int(event.get("attempt") or 0) == attempt
        and (event.get("job_id") or None) == (job_id or None)
        for event in events
    ):
        return record
    if len(events) >= MAX_MEDIA_EVENTS:
        raise MediaEventLogError("media event retention limit reached")
    event = {
        "seq": len(events) + 1,
        "event_id": f"mevt_{uuid.uuid4().hex}",
        "status": status,
        "phase": phase,
        "attempt": attempt,
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "job_id": job_id or None,
    }
    if error:
        event["error"] = str(error)[:500]
    meta["events"] = [*events, event]
    record["ingest_meta"] = meta
    return record


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


def _layout_blocks_incomplete(record: Dict[str, Any]) -> bool:
    """Do not present fallback or known unsupported layout as complete."""
    ingest_meta = record.get("ingest_meta") or {}
    if ingest_meta.get("source_coverage") == "unverified":
        return True
    # Older records had no explicit coverage flag. A light PDF text projection
    # alone never established whole-source coverage.
    if ingest_meta.get("engine") in ("pypdf", "pdfplumber"):
        return True
    try:
        expected = int(ingest_meta.get("block_count") or 0)
    except (TypeError, ValueError):
        return False
    return expected > len(ingest_meta.get("blocks") or [])


def _pdf_capability_snapshot() -> Dict[str, Any]:
    try:
        from ....pipeline.entity_extractor.pdf_engines import describe_pdf_engines

        return describe_pdf_engines()
    except Exception as e:
        return {"status": "unknown", "reason": str(e)[:200]}

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


def _atomic_json_write(path: Path, value: Any, *, indent: Optional[int] = None) -> None:
    """Replace a catalog atomically so a killed worker leaves the prior JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=indent),
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


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
        is_inline = self._ingest_inline(ext)
        ingest_meta = {"ingest_queued_at": now, "ingest_attempt": 1}
        if not is_inline:
            from .jobs import MEDIA_QUEUE, build_media_job_id

            job_id = build_media_job_id("ingest", file_id, 1)
            ingest_meta.update({
                "job_backend": "celery",
                "job_queue": MEDIA_QUEUE,
                "job_id": job_id,
                "job_status": "queued",
            })
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
            "ingest_meta": ingest_meta,
            "content_sha256": content_sha,
            "created_at": now,
            "updated_at": now,
        }
        _append_state_event(
            record,
            status="queued",
            phase="ingest",
            attempt=1,
            job_id=ingest_meta.get("job_id"),
        )
        self._persist_file(record)

        # Phase B — ingest off the HTTP thread (Claude: bytes first, process second)
        if is_inline:
            self.complete_ingest(file_id)
            row = self._load_file(file_id) or record
            return self.public_meta(row)

        try:
            from .jobs import enqueue_media_job

            enqueue_media_job("ingest", file_id, task_id=job_id)
        except Exception as e:
            # OCR can take minutes and must never fall back to the HTTP thread.
            logger.warning("enqueue media_ingest failed: %s", e)
            record["status"] = FileStatus.FAILED.value
            record["error"] = f"解析任务排队失败: {e}"[:500]
            record["ingest_meta"] = {
                **(record.get("ingest_meta") or {}),
                "job_status": "failed",
                "job_error": str(e)[:500],
            }
            _append_state_event(
                record,
                status="failed",
                phase="ingest",
                attempt=int((record.get("ingest_meta") or {}).get("ingest_attempt") or 1),
                job_id=(record.get("ingest_meta") or {}).get("job_id"),
                error=record["error"],
            )
            record["updated_at"] = datetime.now().isoformat(sep=" ")
            self._persist_file(record)
            return self.public_meta(record)

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

        meta = dict(record.get("ingest_meta") or {})
        meta["ingest_started_at"] = datetime.now().isoformat(sep=" ")
        if meta.get("job_backend") == "celery":
            meta["job_status"] = "running"
        record["ingest_meta"] = meta
        _append_state_event(
            record,
            status="ingesting",
            phase="ingest",
            attempt=int(meta.get("ingest_attempt") or 1),
            job_id=meta.get("job_id"),
        )
        self._persist_file(record)

        path = record.get("storage_path") or ""
        name = record.get("filename") or "upload.bin"
        purpose = record.get("purpose") or "chat_attachment"

        # Same bytes already parsed → reuse (scanned resumes often re-uploaded)
        reused = self._try_reuse_by_sha(record)
        if reused:
            return reused

        return self._run_ingest(record, purpose=purpose)

    def schedule_upgrade(self, file_id: str, *, force: bool = False) -> Optional[Dict[str, Any]]:
        """Request a non-blocking repair; preserve any previously usable OCR artifact."""
        record = self._load_file(file_id)
        if not record:
            return None
        record = self._expire_stale_jobs(record)
        if record.get("status") == FileStatus.INGESTING.value:
            return self.public_meta(record)
        full_artifact = next(
            (a for a in self._load_artifacts(file_id) if a.get("kind") == "full_text"),
            {},
        )
        needs_repair = (
            record.get("status") == FileStatus.FAILED.value
            or bool((full_artifact.get("meta") or {}).get("truncated"))
            or _layout_blocks_incomplete(record)
        )
        if not needs_repair:
            return self.public_meta(record)
        meta = dict(record.get("ingest_meta") or {})
        if meta.get("upgrade_status") in ("queued", "running") or (
            not force and meta.get("upgrade_status") in ("failed", "unavailable")
        ):
            return self.public_meta(record)
        path = Path(record.get("storage_path") or "")
        if not path.is_file():
            meta["upgrade_status"] = "unavailable"
            meta["upgrade_error"] = "原文件不存在，无法重新解析"
            record["ingest_meta"] = meta
            self._persist_file(record)
            return self.public_meta(record)
        meta["upgrade_status"] = "queued"
        meta["upgrade_error"] = None
        meta["upgrade_queued_at"] = datetime.now().isoformat(sep=" ")
        meta.pop("upgrade_started_at", None)
        from .jobs import MEDIA_QUEUE, build_media_job_id

        attempt = int(meta.get("upgrade_attempt") or 0) + 1
        job_id = build_media_job_id("upgrade", file_id, attempt)
        meta.update({
            "upgrade_attempt": attempt,
            "upgrade_job_backend": "celery",
            "upgrade_job_queue": MEDIA_QUEUE,
            "upgrade_job_id": job_id,
            "upgrade_job_status": "queued",
        })
        record["ingest_meta"] = meta
        _append_state_event(
            record,
            status="queued",
            phase="upgrade",
            attempt=attempt,
            job_id=job_id,
        )
        record["updated_at"] = datetime.now().isoformat(sep=" ")
        self._persist_file(record)
        try:
            from .jobs import enqueue_media_job

            enqueue_media_job("upgrade", file_id, task_id=job_id)
        except Exception as e:
            meta["upgrade_status"] = "failed"
            meta["upgrade_error"] = f"解析任务排队失败: {e}"[:500]
            meta["upgrade_job_status"] = "failed"
            record["ingest_meta"] = meta
            _append_state_event(
                record,
                status="failed",
                phase="upgrade",
                attempt=attempt,
                job_id=job_id,
                error=meta["upgrade_error"],
            )
            record["updated_at"] = datetime.now().isoformat(sep=" ")
            self._persist_file(record)
        return self.public_meta(record)

    def complete_upgrade(self, file_id: str) -> Optional[Dict[str, Any]]:
        """Worker-only repair. A failed attempt never replaces the old artifacts."""
        record = self._load_file(file_id)
        if not record:
            return None
        meta = dict(record.get("ingest_meta") or {})
        meta["upgrade_status"] = "running"
        meta["upgrade_job_status"] = "running"
        meta["upgrade_started_at"] = datetime.now().isoformat(sep=" ")
        record["ingest_meta"] = meta
        attempt = int(meta.get("upgrade_attempt") or 1)
        _append_state_event(
            record,
            status="ingesting",
            phase="upgrade",
            attempt=attempt,
            job_id=meta.get("upgrade_job_id"),
        )
        self._persist_file(record)
        try:
            self.upgrade_thin_ingest(file_id)
            record = self._load_file(file_id) or record
            full_artifact = next(
                (a for a in self._load_artifacts(file_id) if a.get("kind") == "full_text"),
                {},
            )
            full = full_artifact.get("body_text") or ""
            good = (
                record.get("status") == FileStatus.READY.value
                and bool(full)
                and not bool((full_artifact.get("meta") or {}).get("truncated"))
                and not _layout_blocks_incomplete(record)
            )
            meta = dict(record.get("ingest_meta") or {})
            meta["upgrade_status"] = "ready" if good else "unavailable"
            meta["upgrade_error"] = None if good else "重解析未得到更完整的文本；请检查 OCR 服务能力"
            meta["upgrade_job_status"] = "completed" if good else "failed"
        except Exception as e:
            logger.exception("media upgrade failed for %s", file_id)
            record = self._load_file(file_id) or record
            meta = dict(record.get("ingest_meta") or {})
            meta["upgrade_status"] = "failed"
            meta["upgrade_error"] = str(e)[:500]
            meta["upgrade_job_status"] = "failed"
        record["ingest_meta"] = meta
        _append_state_event(
            record,
            status="ready" if meta.get("upgrade_status") == "ready" else "failed",
            phase="upgrade",
            attempt=attempt,
            job_id=meta.get("upgrade_job_id"),
            error=meta.get("upgrade_error"),
        )
        record["updated_at"] = datetime.now().isoformat(sep=" ")
        self._persist_file(record)
        return self.public_meta(record)

    def upgrade_thin_ingest(self, file_id: str) -> Optional[Dict[str, Any]]:
        """
        Re-run heavy extract when structural source coverage is unverified.
        Worker-only; Ask schedules this without blocking the model request.
        """
        record = self._load_file(file_id)
        if not record:
            return None
        path = record.get("storage_path") or ""
        name = record.get("filename") or "upload.bin"
        full_artifact = next(
            (a for a in self._load_artifacts(file_id) if a.get("kind") == "full_text"),
            {},
        )
        full = full_artifact.get("body_text") or ""
        was_truncated = bool((full_artifact.get("meta") or {}).get("truncated"))
        source_incomplete = _layout_blocks_incomplete(record)

        if not was_truncated and not source_incomplete:
            return self.public_meta(record)
        if source_incomplete and not was_truncated and _ext_name(name) == "pdf":
            from ....pipeline.entity_extractor.pdf_engines import (
                EngineStatus,
                get_mineru_state,
                get_sidecar_state,
            )

            if (
                get_mineru_state(force_probe=True).status != EngineStatus.READY
                and get_sidecar_state(force_probe=True).status != EngineStatus.READY
            ):
                logger.info(
                    "upgrade deferred for %s: no OCR-capable PDF engine available",
                    file_id,
                )
                return self.public_meta(record)
        logger.info(
            "upgrade_thin_ingest file_id=%s chars=%s truncated=%s source_incomplete=%s — forcing resume_parse",
            file_id,
            len(full),
            was_truncated,
            source_incomplete,
        )
        # Parse before replacing persisted artifacts: a missing OCR sidecar must
        # never destroy an older, useful MinerU extraction during repair.
        try:
            artifacts, ingest_meta, ok = ingest_file(str(path), name, purpose="resume_parse")
            if _ext_name(name) == "pdf":
                ingest_meta["ocr_capability"] = _pdf_capability_snapshot()
        except Exception as e:
            logger.warning("upgrade parse failed for %s: %s", file_id, e)
            return self.public_meta(record)
        candidate = next(
            (a.get("body_text") or "" for a in artifacts if a.get("kind") == "full_text"),
            "",
        )
        candidate_record = {**record, "ingest_meta": ingest_meta}
        if (
            not ok
            or not candidate
            or (full and len(candidate) < len(full))
            or (source_incomplete and (
                _layout_blocks_incomplete(candidate_record)
                or len(ingest_meta.get("blocks") or []) < int(
                    (record.get("ingest_meta") or {}).get("block_count") or 0
                )
            ))
        ):
            logger.warning("upgrade parse did not improve %s; preserving prior artifacts", file_id)
            return self.public_meta(record)
        record["ingest_meta"] = {**(record.get("ingest_meta") or {}), **ingest_meta}
        # Artifacts are committed before READY.  A worker loss before the final
        # state write is safely redelivered; READY never points at a missing
        # derivative catalog.
        self._persist_artifacts(file_id, artifacts)
        record["status"] = FileStatus.READY.value
        record["error"] = None
        meta = dict(record.get("ingest_meta") or {})
        _append_state_event(
            record,
            status="ready",
            phase="upgrade",
            attempt=int(meta.get("upgrade_attempt") or 1),
            job_id=meta.get("upgrade_job_id"),
        )
        record["updated_at"] = datetime.now().isoformat(sep=" ")
        self._persist_file(record)
        self._index_sha(record)
        return self.public_meta(record)

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
            if _ext_name(name) == "pdf":
                meta["ocr_capability"] = _pdf_capability_snapshot()
            prior_meta = dict(record.get("ingest_meta") or {})
            record["ingest_meta"] = {**prior_meta, **meta}
            if ok:
                record["status"] = FileStatus.READY.value
                record["error"] = None
            else:
                record["status"] = FileStatus.FAILED.value
                record["error"] = (meta.get("error") or "解析失败")[:500]
            self._persist_artifacts(file_id, artifacts)
            record["ingest_meta"]["job_status"] = "completed" if ok else "failed"
            record["ingest_meta"]["ingest_finished_at"] = datetime.now().isoformat(sep=" ")
            _append_state_event(
                record,
                status="ready" if ok else "failed",
                phase="ingest",
                attempt=int(record["ingest_meta"].get("ingest_attempt") or 1),
                job_id=record["ingest_meta"].get("job_id"),
                error=record.get("error"),
            )
            record["updated_at"] = datetime.now().isoformat(sep=" ")
            self._persist_file(record)
            if ok:
                self._index_sha(record)
        except Exception as e:
            logger.exception("ingest failed for %s", file_id)
            record["status"] = FileStatus.FAILED.value
            record["error"] = str(e)[:500]
            record["ingest_meta"] = {
                **(record.get("ingest_meta") or {}),
                "job_status": "failed",
                "job_error": str(e)[:500],
            }
            _append_state_event(
                record,
                status="failed",
                phase="ingest",
                attempt=int(record["ingest_meta"].get("ingest_attempt") or 1),
                job_id=record["ingest_meta"].get("job_id"),
                error=record["error"],
            )
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
        if _layout_blocks_incomplete(donor):
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
        if any(
            a.get("kind") == "full_text" and (a.get("meta") or {}).get("truncated")
            for a in arts
        ):
            return None
        current_events = list((record.get("ingest_meta") or {}).get("events") or [])
        record["ingest_meta"] = {
            **(donor.get("ingest_meta") or {}),
            **(record.get("ingest_meta") or {}),
            "engine": (donor.get("ingest_meta") or {}).get("engine") or "sha_reuse",
            "reused_from": donor_id,
            "job_status": "completed",
            "events": current_events,
        }
        self._persist_artifacts(record["file_id"], arts)
        record["status"] = FileStatus.READY.value
        record["error"] = None
        _append_state_event(
            record,
            status="ready",
            phase="ingest",
            attempt=int(record["ingest_meta"].get("ingest_attempt") or 1),
            job_id=record["ingest_meta"].get("job_id"),
        )
        record["updated_at"] = datetime.now().isoformat(sep=" ")
        self._persist_file(record)
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
        return self.public_meta(self._expire_stale_jobs(row)) if row else None

    def list_events(
        self,
        file_id: str,
        *,
        after: int = 0,
        limit: int = 100,
    ) -> Optional[Dict[str, Any]]:
        """Replay persisted state transitions strictly after a sequence cursor."""
        after = int(after)
        limit = int(limit)
        if after < 0:
            raise ValueError("after must be >= 0")
        if limit < 1 or limit > 200:
            raise ValueError("limit must be between 1 and 200")
        row = self._load_file(file_id)
        if not row:
            return None
        row = self._expire_stale_jobs(row)
        events = _validated_events(row)
        remaining = [event for event in events if int(event["seq"]) > after]
        page = remaining[:limit]
        next_cursor = int(page[-1]["seq"]) if page else after
        meta = row.get("ingest_meta") or {}
        return {
            "file_id": file_id,
            "events": page,
            "cursor": next_cursor,
            "has_more": len(remaining) > limit,
            "latest_seq": int(events[-1]["seq"]) if events else 0,
            "snapshot": {
                "status": row.get("status"),
                "job_status": meta.get("job_status"),
                "upgrade_status": meta.get("upgrade_status"),
                "upgrade_job_status": meta.get("upgrade_job_status"),
                "updated_at": row.get("updated_at"),
            },
            "source": "agent_file.ingest_meta.events",
        }

    def _expire_stale_jobs(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """Bound states left behind by a daemon-thread/process interruption."""
        meta = dict(row.get("ingest_meta") or {})
        now = datetime.now()

        def stale(*keys: str) -> bool:
            raw = next((meta.get(key) for key in keys if meta.get(key)), None)
            raw = raw or row.get("updated_at") or row.get("created_at")
            if raw:
                try:
                    return now - datetime.fromisoformat(str(raw)) > JOB_STALE_AFTER
                except (TypeError, ValueError):
                    pass
            return False

        changed = False
        durable_ingest = meta.get("job_backend") == "celery" and bool(meta.get("job_id"))
        if not durable_ingest and row.get("status") == FileStatus.INGESTING.value and stale(
            "ingest_started_at", "ingest_queued_at"
        ):
            row["status"] = FileStatus.FAILED.value
            row["error"] = "解析任务超时或服务重启；原文件已保留，可重试解析"
            _append_state_event(
                row,
                status="failed",
                phase="ingest",
                attempt=int(meta.get("ingest_attempt") or 1),
                job_id=meta.get("job_id"),
                error=row["error"],
            )
            meta = dict(row.get("ingest_meta") or {})
            changed = True
        durable_upgrade = (
            meta.get("upgrade_job_backend") == "celery"
            and bool(meta.get("upgrade_job_id"))
        )
        if not durable_upgrade and meta.get("upgrade_status") in ("queued", "running") and stale(
            "upgrade_started_at", "upgrade_queued_at"
        ):
            meta["upgrade_status"] = "failed"
            meta["upgrade_error"] = "重解析任务超时或服务重启；原有文本已保留，可手动重试"
            row["ingest_meta"] = meta
            _append_state_event(
                row,
                status="failed",
                phase="upgrade",
                attempt=int(meta.get("upgrade_attempt") or 1),
                job_id=meta.get("upgrade_job_id"),
                error=meta["upgrade_error"],
            )
            meta = dict(row.get("ingest_meta") or {})
            changed = True
        if changed:
            row["ingest_meta"] = meta
            row["updated_at"] = now.isoformat(sep=" ")
            self._persist_file(row)
        return row

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
        events = _validated_events(row)
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
            "layout_incomplete": _layout_blocks_incomplete(row),
            "upgrade_status": (row.get("ingest_meta") or {}).get("upgrade_status"),
            "upgrade_error": (row.get("ingest_meta") or {}).get("upgrade_error"),
            "job_id": (row.get("ingest_meta") or {}).get("job_id"),
            "job_backend": (row.get("ingest_meta") or {}).get("job_backend"),
            "job_status": (row.get("ingest_meta") or {}).get("job_status"),
            "upgrade_job_id": (row.get("ingest_meta") or {}).get("upgrade_job_id"),
            "upgrade_job_status": (row.get("ingest_meta") or {}).get("upgrade_job_status"),
            "event_cursor": int(events[-1]["seq"]) if events else 0,
            "ocr_capability": (row.get("ingest_meta") or {}).get("ocr_capability"),
            "created_at": row.get("created_at"),
        }

    # ── persistence: disk catalog primary, MySQL best-effort ─────

    def _persist_file(self, record: Dict[str, Any]) -> None:
        # Disk catalog (source of truth)
        try:
            _atomic_json_write(_meta_path(record["file_id"]), record, indent=0)
        except Exception as e:
            logger.warning("disk catalog write failed: %s", e)
            raise
        with _mem_lock:
            _mem_files[record["file_id"]] = dict(record)

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
        try:
            _atomic_json_write(_artifacts_path(file_id), artifacts)
        except Exception as e:
            logger.warning("disk artifacts write failed: %s", e)
            raise
        with _mem_lock:
            _mem_artifacts[file_id] = list(artifacts)

        try:
            # Redelivery replaces one logical derivative set; it must not append
            # duplicate DB rows after a worker loss.
            self.mysql.execute(
                "DELETE FROM agent_file_artifact WHERE file_id = :file_id",
                {"file_id": file_id},
                commit=True,
            )
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
        # Disk is the cross-process source of truth. The Flask publisher and
        # Celery worker have separate memory maps, so consulting memory first
        # would leave GET polling permanently stuck at the pre-worker state.
        mp = _meta_path(file_id)
        if mp.is_file():
            try:
                d = json.loads(mp.read_text(encoding="utf-8"))
                with _mem_lock:
                    _mem_files[file_id] = d
                return dict(d)
            except Exception as e:
                logger.warning("disk catalog read failed: %s", e)
        with _mem_lock:
            if file_id in _mem_files:
                return dict(_mem_files[file_id])
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
        # Same cross-process rule as _load_file: a worker may have replaced the
        # derivative catalog since this Flask process last read it.
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
        with _mem_lock:
            if file_id in _mem_artifacts:
                return list(_mem_artifacts[file_id])
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
