from __future__ import annotations

# 面向 block-specific 提取的最小可用子契约（先确保 0.6B 能稳定提取）

EDUCATION_SCHEMA = {
    "type": "object",
    "properties": {
        "school": {"type": ["string", "null"]},
        "major": {"type": ["string", "null"]},
        "degree": {"type": ["string", "null"]},
        "start_date": {"type": ["string", "null"]},
        "end_date": {"type": ["string", "null"]},
    },
    "required": ["school", "major", "degree", "start_date", "end_date"],
    "additionalProperties": False,
}

PROJECT_SCHEMA = {
    "type": "object",
    "properties": {
        "project_name": {"type": ["string", "null"]},
        "project_role": {"type": ["string", "null"]},
        "start_date": {"type": ["string", "null"]},
        "end_date": {"type": ["string", "null"]},
        "description": {"type": ["string", "null"]},
    },
    "required": ["project_name", "project_role", "start_date", "end_date", "description"],
    "additionalProperties": False,
}

EXTRACTION_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "educations": {"type": "array", "items": EDUCATION_SCHEMA},
        "projects": {"type": "array", "items": PROJECT_SCHEMA},
        "skills": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["educations", "projects", "skills"],
    "additionalProperties": False,
}
