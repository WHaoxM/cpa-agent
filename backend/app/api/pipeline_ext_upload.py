"""
Pipeline_ext Upload 子模块

L0 文件上传解析 API (2 路由):
- POST /parse/upload/resume  上传并解析简历文件 (MinerU + YOLOv10 + OCR)
- POST /parse/upload/jd      上传并解析 JD 文件

依赖: ..pipeline.entity_extractor.l0_full_parser (L0FullParser)
      ..pipeline.entity_extractor.llm_skill_extractor (extract_skills_with_relations)
"""

import os
import tempfile
import uuid
from pathlib import Path

from flask import request, jsonify

from ._helpers import safe_error
from .pipeline_ext_common import pipeline_ext_bp, logger

# 文件类型白名单
_ALLOWED_EXT = {'pdf', 'docx', 'doc', 'txt', 'png', 'jpg', 'jpeg', 'bmp', 'webp', 'tiff'}


@pipeline_ext_bp.route('/parse/upload/resume', methods=['POST'])
def parse_resume_file_api():
    """
    L0: 上传并解析简历文件 (MinerU + YOLOv10 + OCR)

    Form Data:
        file: PDF/DOCX/TXT/图片文件
        extract_skills: true|false (可选, 默认false)
    """
    if 'file' not in request.files:
        return jsonify({"success": False, "error": "请上传文件"}), 400

    file = request.files['file']
    extract_skills = request.form.get('extract_skills', 'false').lower() == 'true'

    if file.filename == '':
        return jsonify({"success": False, "error": "文件名为空"}), 400

    # ── 文件类型白名单校验 ──
    _ext = Path(file.filename).suffix.lstrip('.').lower()
    if _ext and _ext not in _ALLOWED_EXT:
        return jsonify({"success": False, "error": f"不支持的文件格式: {_ext}"}), 400

    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=Path(file.filename).suffix) as tmp:
            file.save(tmp.name)
            temp_path = tmp.name

        from ..pipeline.entity_extractor.l0_full_parser import L0FullParser
        parser = L0FullParser()

        l0_result = parser.parse_file(temp_path)

        if not l0_result.success:
            return jsonify(safe_error("简历解析", l0_result.error, message="文件解析失败，请检查文件格式")), 500

        # Structure truth: never silently truncate raw resume text (old [:5000] dropped 教育经历).
        full_text = (l0_result.text or "").strip()
        response_data = {
            "student_id": f"student_{uuid.uuid4().hex[:8]}",
            "text": full_text,
            "raw_text": full_text,
            "text_chars": len(full_text),
            "truncated": False,
            "block_count": len(l0_result.blocks),
            "blocks": [
                {
                    "block_id": b.block_id,
                    "block_type": b.block_type,
                    "text": b.text[:500],
                    "confidence": b.confidence,
                    "source": b.source,
                    "page": b.page
                }
                for b in l0_result.blocks[:80]
            ],
            "metadata": {
                **l0_result.metadata,
                "engine": l0_result.engine,
                "file_size": os.path.getsize(temp_path),
                "text_chars": len(full_text),
            }
        }

        if extract_skills and l0_result.text:
            from ..pipeline.entity_extractor.llm_skill_extractor import extract_skills_with_relations
            skills_result = extract_skills_with_relations(l0_result.text, use_llm=True)
            response_data["skills"] = skills_result.get("skills", [])
            response_data["skill_count"] = len(skills_result.get("skills", []))

        return jsonify({
            "success": True,
            "data": response_data
        })

    except Exception as e:
        return jsonify(safe_error("简历文件解析", e, message="服务器内部错误")), 500
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


@pipeline_ext_bp.route('/parse/upload/jd', methods=['POST'])
def parse_jd_file_api():
    """
    L0: 上传并解析JD文件

    Form Data:
        file: PDF/DOCX/TXT/图片文件
        extract_skills: true|false
    """
    if 'file' not in request.files:
        return jsonify({"success": False, "error": "请上传文件"}), 400

    file = request.files['file']
    extract_skills = request.form.get('extract_skills', 'false').lower() == 'true'

    if file.filename == '':
        return jsonify({"success": False, "error": "文件名为空"}), 400

    # ── 文件类型白名单校验 ──
    _ext = Path(file.filename).suffix.lstrip('.').lower()
    if _ext and _ext not in _ALLOWED_EXT:
        return jsonify({"success": False, "error": f"不支持的文件格式: {_ext}"}), 400

    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=Path(file.filename).suffix) as tmp:
            file.save(tmp.name)
            temp_path = tmp.name

        from ..pipeline.entity_extractor.l0_full_parser import L0FullParser
        parser = L0FullParser()

        l0_result = parser.parse_file(temp_path)

        if not l0_result.success:
            return jsonify(safe_error("JD解析", l0_result.error, message="文件解析失败，请检查文件格式")), 500

        response_data = {
            "text": l0_result.text[:5000] if l0_result.text else "",
            "block_count": len(l0_result.blocks),
            "blocks": [
                {
                    "block_id": b.block_id,
                    "block_type": b.block_type,
                    "text": b.text[:500],
                    "confidence": b.confidence,
                    "source": b.source,
                    "page": b.page
                }
                for b in l0_result.blocks
            ],
            "metadata": {
                **l0_result.metadata,
                "engine": l0_result.engine,
                "file_size": os.path.getsize(temp_path)
            }
        }

        if extract_skills and l0_result.text:
            from ..pipeline.entity_extractor.llm_skill_extractor import extract_skills_with_relations
            skills_result = extract_skills_with_relations(l0_result.text, use_llm=True)
            response_data["skills"] = skills_result.get("skills", [])
            response_data["skill_count"] = len(skills_result.get("skills", []))

        return jsonify({
            "success": True,
            "data": response_data
        })

    except Exception as e:
        return jsonify(safe_error("JD文件解析", e, message="服务器内部错误")), 500
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)
