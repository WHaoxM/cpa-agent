"""Private, untrusted admin source staging; not a job publication or graph ingest."""

import hashlib
import json
import os
import stat
import uuid
from pathlib import Path

from ..config import Config


MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_STAGED_JOBS = 1000
_EXTENSIONS = {"dataset": ".json", "knowledge-base": ".txt"}


def _root() -> Path:
    root = (Path(Config.UPLOAD_FOLDER) / "admin_sources").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _path(source_id: str, kind: str) -> Path:
    if kind not in _EXTENSIONS or not source_id.isascii() or not source_id.replace("_", "").isalnum():
        raise ValueError("无效的管理员来源标识")
    prefix = "ds_" if kind == "dataset" else "kb_"
    if not source_id.startswith(prefix):
        raise ValueError("管理员来源类型不匹配")
    return _root() / f"{source_id}{_EXTENSIONS[kind]}"


def _parse(kind: str, content: bytes):
    try:
        decoded = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("仅支持 UTF-8 内容") from exc
    if not decoded.strip():
        raise ValueError("上传内容不能为空")
    if kind == "knowledge-base":
        return decoded, 0
    try:
        value = json.loads(decoded, parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError("岗位暂存文件必须是有效 JSON") from exc
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_STAGED_JOBS:
        raise ValueError("岗位暂存文件须含 1 至 1000 条 JSON 数组记录")
    for index, item in enumerate(value):
        title = item.get("title") if isinstance(item, dict) else None
        if not isinstance(title, str) or not title.strip() or len(title) > 200:
            raise ValueError(f"第 {index + 1} 条岗位缺少有效 title")
    return value, len(value)


def stage_source(source_id: str, kind: str, upload):
    """Validate before writing, then atomically expose one private source file."""
    filename = str(getattr(upload, "filename", "") or "").replace("\\", "/").split("/")[-1]
    if Path(filename).suffix.lower() != _EXTENSIONS[kind]:
        raise ValueError(f"仅支持 {_EXTENSIONS[kind]} 文件")
    content = upload.stream.read(MAX_SOURCE_BYTES + 1)
    if len(content) > MAX_SOURCE_BYTES:
        raise ValueError("上传文件超过 2 MiB 上限")
    _, staged_count = _parse(kind, content)
    digest = hashlib.sha256(content).hexdigest()
    destination = _path(source_id, kind)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(content)
        if destination.exists() or destination.is_symlink():
            raise FileExistsError("管理员来源 ID 冲突")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return {"file_path": str(destination), "file_size": len(content),
            "content_sha256": digest, "staged_record_count": staged_count}


def checked_path(source_id: str, kind: str, file_path: str) -> Path:
    expected = _path(source_id, kind)
    # A persisted path is never allowed to redirect this admin API outside its own root.
    if not file_path or Path(file_path) != expected or expected.is_symlink():
        raise ValueError("管理员来源文件路径无效")
    return expected


def read_source(source_id: str, kind: str, row: dict):
    path = checked_path(source_id, kind, row.get("file_path"))
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("管理员来源不是常规文件")
        content = stream.read(MAX_SOURCE_BYTES + 1)
    if (len(content) > MAX_SOURCE_BYTES or len(content) != row.get("file_size")
            or hashlib.sha256(content).hexdigest() != row.get("content_sha256")):
        raise ValueError("管理员来源文件摘要或长度不匹配")
    parsed, count = _parse(kind, content)
    if kind == "dataset" and count != row.get("staged_record_count"):
        raise ValueError("管理员来源记录数不匹配")
    return parsed


def remove_staged_source(path: str) -> None:
    Path(path).unlink(missing_ok=True)
