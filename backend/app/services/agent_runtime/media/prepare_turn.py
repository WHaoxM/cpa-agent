"""
Prepare a user turn: content parts → mount → visible STM text.

Shared by API (persist history) and ModeRuntime (model prefix).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .context_mount import (
    build_attachment_mount_card,
    normalize_content_parts,
    user_visible_text,
)
from .session_resources import resolve_attachment_ids
from .voice_turn import get_voice_turn_service


def prepare_user_turn(
    message: str,
    *,
    session_id: Optional[str] = None,
    student_id: Optional[str] = None,
    attachment_ids: Optional[List[str]] = None,
    content_parts: Optional[List[Dict[str, Any]]] = None,
    voice_turn_id: Optional[str] = None,
    mount: bool = True,
) -> Dict[str, Any]:
    """
    Returns:
      visible_text, parts, turn_file_ids, attach_card, mount_errors, voice_deduped
    """
    parts: List[Dict[str, Any]] = list(content_parts or [])
    voice_deduped = False
    msg = (message or "").strip()

    if voice_turn_id:
        vp = get_voice_turn_service().as_content_part(
            voice_turn_id, student_id=student_id, session_id=session_id,
        )
        if vp is None:
            raise ValueError("语音转写尚未就绪")
        if vp:
            vt = (vp.get("text") or "").strip()
            # Avoid duplicating transcript already present in message
            if vt and msg and (vt == msg or vt in msg):
                voice_deduped = True
                # keep turn_id metadata only — don't append second voice part
                if not any(p.get("type") == "voice_transcript" for p in parts):
                    parts.append({
                        "type": "voice_transcript",
                        "text": "",  # empty: already in text part
                        "turn_id": voice_turn_id,
                        "file_id": vp.get("file_id") or "",
                    })
            else:
                parts.append(vp)
                if not msg and vt:
                    msg = vt

    parts = normalize_content_parts(msg, parts, attachment_ids)

    # Explicit task references are re-read server-side before either STM write
    # or model invocation. A forged/cross-owner id rejects the whole turn.
    task_ids = list(dict.fromkeys(
        p["task_id"] for p in parts if p.get("type") == "artifact_ref"
    ))
    resource_uris = list(dict.fromkeys(
        p["uri"] for p in parts if p.get("type") == "resource_ref"
    ))
    if len(task_ids) + len(resource_uris) > 4:
        raise ValueError("单轮最多引用 4 个业务资料或任务")
    verified_refs = []
    verified_resources = []
    resource_overviews = []
    if task_ids or resource_uris:
        if not student_id:
            raise PermissionError("业务资料引用需要服务端身份")
        from ...db import get_mysql_client
        from ..pipeline_reference import resolve_pipeline_task_ref
        mysql = get_mysql_client()
        verified_refs = [resolve_pipeline_task_ref(mysql, student_id=student_id, task_id=tid)
                         for tid in task_ids]
        # A task ref is initially only a task/status card.  For completed,
        # validated L0/L2 tasks, bind it to the canonical resource only when
        # the same Principal's current catalog still has that exact task id as
        # its version.  Otherwise mount an unavailable sentinel so Ask's
        # ordinary planner cannot silently substitute newer current data.
        if task_ids:
            eligible = [ref for ref in verified_refs if ref.get("canonical_resource_uri")]
            bound_task_ids = set()
            if eligible:
                try:
                    from ..resource_catalog import list_platform_resources
                    catalog_result = list_platform_resources(mysql, student_id=student_id)
                    current = {
                        item.get("uri"): item for item in catalog_result.get("resources", [])
                        if isinstance(item, dict)
                    }
                except Exception:
                    current = {}
                for ref in eligible:
                    uri = ref["canonical_resource_uri"]
                    item = current.get(uri) or {}
                    if item.get("status") == "completed" and item.get("version") == ref["task_id"]:
                        verified_resources.append({"uri": uri, "version": ref["task_id"]})
                        bound_task_ids.add(ref["task_id"])
            # Non-eligible tasks (including partial L2), stale L2 tasks, and
            # unavailable catalogs remain explicit status-only references.
            for ref in verified_refs:
                if ref["task_id"] not in bound_task_ids:
                    verified_resources.append({
                        "task_id": ref["task_id"],
                        "status": "reference_not_current",
                        "task_status": ref["status"],
                    })
        if resource_uris:
            from ..resource_catalog import read_platform_resource
            for uri in resource_uris:
                overview = read_platform_resource(mysql, student_id=student_id, uri=uri)
                if not isinstance(overview, dict) or overview.get("success") is not True or overview.get("status") in {
                    "unavailable", "not_available", "failed", "empty", "denied", "stale",
                }:
                    raise LookupError("当前用户无法引用该业务资料")
                verified_resources.append({
                    "uri": uri, "status": overview.get("status"),
                    "version": overview.get("version"),
                    "coverage": overview.get("coverage"),
                })
                resource_overviews.append(overview)
        # Deduplicate server-generated and explicit resource references while
        # keeping a hard cap compatible with build_evidence_pack.
        deduped = []
        seen_resources = set()
        for ref in verified_resources:
            key = (ref.get("uri"), ref.get("section"), ref.get("dimension"), ref.get("version"))
            if key not in seen_resources:
                seen_resources.add(key)
                deduped.append(ref)
        verified_resources = deduped
        if len(verified_resources) > 4:
            raise ValueError("单轮最多引用 4 个业务资料或任务")

    # Clean empty voice_transcript text parts for visible text
    mount_errors: List[str] = []
    turn_ids: List[str] = []
    for p in parts:
        fid = p.get("file_id")
        if fid and fid not in turn_ids:
            turn_ids.append(str(fid))

    if mount and session_id and turn_ids:
        try:
            resolve_attachment_ids(session_id, turn_ids, student_id=student_id)
        except (PermissionError, LookupError) as e:
            # Cross-owner or absent files must stop before STM/model card
            # construction. Only an owned file's ingest/readiness failure is
            # a recoverable partial mount.
            raise LookupError("附件不存在或无访问权限") from e
        except ValueError as e:
            mount_errors.append(str(e))

    # Build visible STM without empty [语音] lines
    visible_parts = []
    for p in parts:
        if p.get("type") == "voice_transcript" and not (p.get("text") or "").strip():
            continue
        visible_parts.append(p)
    visible = user_visible_text(visible_parts) if visible_parts else (msg or "")

    card = build_attachment_mount_card(
        session_id, parts, turn_file_ids=turn_ids, student_id=student_id,
    )
    if verified_refs:
        import json
        reference_card = "【服务端已核验的任务引用；不是完整业务成果】\n" + "\n".join(
            json.dumps(ref, ensure_ascii=False, sort_keys=True) for ref in verified_refs
        )
        card = "\n".join(item for item in (card, reference_card) if item)
    if verified_resources:
        import json
        reference_card = "【服务端已核验的业务资料引用；仅为概览，不是全文】\n" + "\n".join(
            json.dumps(ref, ensure_ascii=False, sort_keys=True) for ref in verified_resources
        )
        card = "\n".join(item for item in (card, reference_card) if item)

    return {
        "visible_text": visible,
        "parts": parts,
        "turn_file_ids": turn_ids,
        "attach_card": card,
        "mount_errors": mount_errors,
        "voice_deduped": voice_deduped,
        "verified_task_refs": verified_refs,
        "verified_resource_refs": verified_resources,
        "resource_overviews": resource_overviews,
    }
