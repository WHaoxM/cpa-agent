"""Shared field policy key sets for model/public projections."""

from __future__ import annotations

NEVER_KEYS = frozenset({
    "phone", "email", "mobile", "tel", "telephone",
    "id_card", "idcard", "身份证", "password", "secret", "token",
    "api_key", "access_token", "refresh_token",
    "profile_json", "posting_json", "portrait_json", "report_json",
    "raw_text", "resume_text", "content_full",
})

PUBLIC_KEYS = frozenset({
    "student_id", "job_id", "portrait_id", "report_id", "session_id",
    "task_id", "name", "school", "major", "degree", "status",
    "position_name", "company_name",
})
