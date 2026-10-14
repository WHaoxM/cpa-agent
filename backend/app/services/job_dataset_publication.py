"""Resolve one untrusted staged dataset record for human-reviewed publication.

This never promotes a dataset or KB to an official source. The immutable file
and selected record digests bind what the administrator saw to what is written.
"""

import re

from sqlalchemy import text

from .admin_source_store import read_source
from .job_publication import content_sha256


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_DATASET_ID = re.compile(r"ds_[0-9a-f]{12}\Z")
_PROVENANCE_FIELDS = frozenset({
    "staged_dataset_id", "staged_dataset_record_index", "staged_dataset_sha256",
    "staged_record_sha256", "source_provenance_kind",
})


class DatasetCandidateChanged(ValueError):
    """The selected persisted source no longer matches the reviewed version."""


class DatasetCandidateUnavailable(Exception):
    """The persisted source cannot be read or integrity-verified."""


def validate_reference(dataset_id, record_index, expected_dataset_sha256,
                       expected_record_sha256=None):
    if not isinstance(dataset_id, str) or not _DATASET_ID.fullmatch(dataset_id):
        raise ValueError("source_dataset_id 必须是有效 ds_ 标识")
    if type(record_index) is not int or record_index < 0 or record_index >= 1000:
        raise ValueError("source_record_index 必须是 0 至 999 的整数")
    if (not isinstance(expected_dataset_sha256, str)
            or not _SHA256.fullmatch(expected_dataset_sha256)):
        raise ValueError("expected_dataset_sha256 必须是 SHA-256")
    if (expected_record_sha256 is not None
            and (not isinstance(expected_record_sha256, str)
                 or not _SHA256.fullmatch(expected_record_sha256))):
        raise ValueError("expected_record_sha256 必须是 SHA-256")


def resolve_staged_candidate(conn, *, dataset_id, record_index,
                             expected_dataset_sha256, expected_record_sha256=None,
                             lock=False):
    """Read DB + exact file bytes each time; conn may be the publish transaction."""
    validate_reference(dataset_id, record_index, expected_dataset_sha256,
                       expected_record_sha256)
    row = conn.execute(text(
        "SELECT id,status,count,file_path,file_size,content_sha256,staged_record_count "
        "FROM job_dataset WHERE id=:id" + (" FOR UPDATE" if lock else "")),
        {"id": dataset_id}).mappings().first()
    if (not row or row["status"] != "pending" or row["count"] != 0
            or not row["file_path"] or row["content_sha256"] != expected_dataset_sha256):
        raise DatasetCandidateChanged("暂存数据集不存在、状态或摘要已改变，请重新上传并预检")
    try:
        records = read_source(dataset_id, "dataset", dict(row))
    except (OSError, ValueError) as exc:
        raise DatasetCandidateUnavailable("暂存数据集文件不可用或完整性校验失败") from exc
    if record_index >= len(records):
        raise DatasetCandidateChanged("暂存数据集的记录位置已改变")
    record = records[record_index]
    record_sha256 = content_sha256(record)
    if expected_record_sha256 is not None and record_sha256 != expected_record_sha256:
        raise DatasetCandidateChanged("暂存数据集的岗位记录已改变，请重新预检")
    candidate = record.get("snapshot") if isinstance(record, dict) else None
    if (not isinstance(candidate, dict) or set(candidate) & _PROVENANCE_FIELDS
            or record.get("title") != candidate.get("position_name")):
        raise ValueError("选中记录须包含 title 与同名的完整 snapshot，且不得自报来源绑定")
    bound = {**candidate,
             "source_provenance_kind": "admin_staged_dataset",
             "staged_dataset_id": dataset_id,
             "staged_dataset_record_index": record_index,
             "staged_dataset_sha256": expected_dataset_sha256,
             "staged_record_sha256": record_sha256}
    return bound, record_sha256


def has_dataset_provenance(candidate):
    return isinstance(candidate, dict) and bool(set(candidate) & _PROVENANCE_FIELDS)
