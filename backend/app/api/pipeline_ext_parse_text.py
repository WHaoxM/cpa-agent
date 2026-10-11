"""
Pipeline_ext Parse_text 子模块

L0 轻量文本入口（不做模型画像抽取）。
结构化学生档案请走 POST /api/pipeline/ext/nsle/resume（NS-LE LLM）。
"""

import json

from flask import request, jsonify

from ._helpers import ScopeError, resolve_request_scope, safe_error, scope_error_response
from .pipeline_ext_common import pipeline_ext_bp


@pipeline_ext_bp.route('/parse/text', methods=['POST'])
def parse_text_resume():
    """
    L0: 接收简历文本并落库；不做 LLM 实体抽取。

    Body:
        {"text": "...", "source": "career-navigation"}
    """
    try:
        scope = resolve_request_scope()
    except ScopeError as e:
        return scope_error_response(e)

    try:
        data = request.get_json() or {}
        text = data.get('text', '')
        student_id = scope.student_id

        if not text or len(text.strip()) < 20:
            return jsonify({"success": False, "error": "简历文本过短或为空"}), 400

        lines = text.strip().split('\n')
        name = lines[0].strip() if lines else ""
        name = name.replace("（未填写姓名）", "").strip()

        parsed_result = {
            "student_id": student_id,
            "name": name or "未命名",
            "raw_text": text,
            "text": text,
            "text_length": len(text),
            "line_count": len(lines),
            "next_recommended": [
                "POST /api/pipeline/ext/nsle/resume",
                "POST /api/pipeline/ext/extract/skills",
                "POST /api/career/infer-role",
                "POST /api/pipeline/ext/portrait/student",
            ],
        }

        from ..services.db import get_mysql_client
        mysql = get_mysql_client()
        sql = """
            INSERT INTO student_profile
            (student_id, name, phone, email, profile_json, source_type, created_at)
            VALUES (:student_id, :name, :phone, :email, :profile_json, 'text', NOW())
            ON DUPLICATE KEY UPDATE
            name = VALUES(name), profile_json = VALUES(profile_json), updated_at = NOW()
            """
        mysql.execute_write(sql, {
            "student_id": student_id,
            "name": parsed_result["name"],
            "phone": "",
            "email": "",
            "profile_json": json.dumps({"raw_text": text, "text_length": len(text)}, ensure_ascii=False),
        })

        return jsonify({"success": True, "data": parsed_result})

    except Exception as e:
        return jsonify(safe_error("文本解析", e, message="解析失败，请稍后重试")), 500
