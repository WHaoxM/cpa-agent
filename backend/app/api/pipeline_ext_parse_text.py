"""
Pipeline_ext Parse_text 子模块

L0 文本解析 API (1 路由, ResumeBuilder 专用):
- POST /parse/text  解析前端生成的简历文本

依赖: ..services.db (MySQL, 可选保存)
"""

import json
import uuid

from flask import request, jsonify

from ._helpers import safe_error
from .pipeline_ext_common import pipeline_ext_bp, logger


@pipeline_ext_bp.route('/parse/text', methods=['POST'])
def parse_text_resume():
    """
    L0: 解析前端生成的简历文本（ResumeBuilder表单生成）

    Body:
        {
            "text": "简历文本内容（多行格式）",
            "student_id": "可选，用于关联",
            "source": "resume_builder"
        }

    Returns:
        类似 PDF 解析结果，但不需要 OCR
    """
    try:
        data = request.get_json() or {}
        text = data.get('text', '')
        student_id = data.get('student_id') or f"rb_{uuid.uuid4().hex[:8]}"

        if not text or len(text.strip()) < 50:
            return jsonify({"success": False, "error": "简历文本过短或为空"}), 400

        # 基本结构化解析（无需 MinerU）
        lines = text.strip().split('\n')

        # 提取基本信息
        name = lines[0].strip() if lines else ""
        name = name.replace("（未填写姓名）", "").strip()

        # 查找联系信息
        phone, email = "", ""
        for line in lines:
            if "电话" in line or "手机" in line:
                phone = line.split("：")[-1].strip() if "：" in line else line.strip()
            if "邮箱" in line or "email" in line.lower():
                email = line.split("：")[-1].strip() if "：" in line else line.strip()

        # 查找教育信息
        education = []
        in_edu_section = False
        for line in lines:
            if "【教育经历】" in line or "教育经历" in line:
                in_edu_section = True
                continue
            if in_edu_section and line.strip() and not line.startswith("【"):
                parts = line.split("|")
                if len(parts) >= 2:
                    education.append({
                        "school": parts[0].strip(),
                        "major": parts[1].strip() if len(parts) > 1 else "",
                        "degree": parts[2].strip() if len(parts) > 2 else "",
                        "period": parts[3].strip() if len(parts) > 3 else ""
                    })
            if in_edu_section and (line.startswith("【") or line.strip() == ""):
                in_edu_section = False

        # 查找技能
        skills = []
        for line in lines:
            if "【专业技能】" in line or "技能" in line:
                continue
            if "【" in line:
                continue
            if any(kw in line for kw in ["Python", "Java", "JavaScript", "Vue", "React", "SQL", "Linux", "Docker"]):
                skills_found = [s.strip() for s in line.replace("·", "|").replace("、", "|").split("|") if s.strip()]
                skills.extend(skills_found)

        # 构建返回结果
        parsed_result = {
            "student_id": student_id,
            "name": name or "未命名",
            "contact": {"phone": phone, "email": email},
            "education": education,
            "skills": list(set(skills))[:20],  # 去重，最多20个
            "raw_text": text,
            "text_length": len(text),
            "line_count": len(lines)
        }

        # 可选：保存到 student_profile 表
        from ..services.db import get_mysql_client
        mysql = get_mysql_client()
        try:
            sql = """
            INSERT INTO student_profile
            (student_id, name, phone, email, profile_json, source_type, created_at)
            VALUES (:student_id, :name, :phone, :email, :profile_json, 'text', NOW())
            ON DUPLICATE KEY UPDATE
            name = VALUES(name), phone = VALUES(phone), email = VALUES(email),
            profile_json = VALUES(profile_json), updated_at = NOW()
            """
            mysql.execute_write(sql, {
                "student_id": student_id,
                "name": parsed_result["name"],
                "phone": phone,
                "email": email,
                "profile_json": json.dumps(parsed_result)
            })
        except Exception as e:
            logger.warning(f"保存文本简历失败: {e}")

        return jsonify({
            "success": True,
            "data": parsed_result,
            "next_steps": [
                "调用 /api/pipeline/ext/nsle/resume 进行实体抽取",
                "调用 /api/pipeline/ext/portrait/student 生成画像"
            ]
        })

    except Exception as e:
        return jsonify(safe_error("文本解析", e, message="解析失败，请稍后重试")), 500
