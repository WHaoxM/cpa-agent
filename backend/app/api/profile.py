"""
画像相关 API 路由
/api/profile — 学生画像、岗位画像的 CRUD 和生成
"""

import uuid
import re
from flask import request, jsonify

from . import profile_bp
from ._helpers import build_dimensions_d1_d4, build_sub_dimensions, parse_json_field, safe_error
from ..services.db import get_mysql_client
from ..utils.logger import get_logger
from ..exceptions import ErrorCode

logger = get_logger('cpa-agent.api.profile')


def mask_phone(phone: str) -> str:
    """电话脱敏: 13812345678 -> 138****5678"""
    if not phone:
        return phone
    phone = str(phone).strip()
    if len(phone) < 7:
        return phone
    return f"{phone[:3]}****{phone[-4:]}"


@profile_bp.route('/student/upload', methods=['POST'])
def upload_resume():
    """上传简历文件, 保存到持久化目录, 触发 L0 解析管线"""
    if 'file' not in request.files:
        return jsonify({
            "success": False,
            "error": "未上传文件"
        }), 400

    file = request.files['file']
    student_id = request.form.get('student_id')
    session_id = request.form.get('session_id')

    if not student_id:
        student_id = f"stu_{uuid.uuid4().hex[:12]}"

    filename = file.filename
    file_ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else 'pdf'

    # ── 文件类型白名单校验 ──
    ALLOWED_EXTENSIONS = {'pdf', 'docx', 'doc', 'txt', 'png', 'jpg', 'jpeg', 'bmp', 'webp', 'tiff'}
    if file_ext not in ALLOWED_EXTENSIONS:
        return jsonify({"success": False, "error": f"不支持的文件格式: {file_ext}，仅支持 {', '.join(sorted(ALLOWED_EXTENSIONS))}"}), 400

    # ── D-06: 持久化保存文件 ──
    import os
    from ..config import Config
    upload_dir = os.path.join(Config.UPLOAD_FOLDER, student_id)
    os.makedirs(upload_dir, exist_ok=True)
    save_path = os.path.join(upload_dir, f"resume_{uuid.uuid4().hex[:8]}{os.path.splitext(filename)[1] or '.pdf'}")
    file.save(save_path)

    task_id = f"task_{uuid.uuid4().hex[:12]}"

    logger.info(f"简历上传: student_id={student_id}, filename={filename}, saved={save_path}")

    # ── D-06: 创建 pipeline_task 记录 + 触发 L0 管线 ──
    parse_result = None
    try:
        # 同步触发 L0 解析 (PipelineRunner 内部会创建 pipeline_task 记录)
        from ..services.pipeline import PipelineRunner, PipelineLevel
        runner = PipelineRunner()
        parse_result = runner.run(
            pipeline=PipelineLevel.L0_PARSE_RESUME,
            params={"student_id": student_id, "file_path": save_path},
        )
    except Exception as e:
        logger.error(f"L0 管线触发失败: {e}")
        # 非阻断: 文件已保存, 返回 pending 状态让前端可轮询
        parse_result = {"status": "pending", "error": "解析任务已提交，请稍后查询结果"}

    # ── D-02: 回写 student_id 到 session ──
    if session_id:
        try:
            from ..services.session_manager import get_session_manager
            session_mgr = get_session_manager()
            session = session_mgr.get_session(session_id)
            if session and not session.student_id:
                session_mgr.update_session(session_id, student_id=student_id)
        except Exception as e:
            logger.warning(f"Session student_id 回写失败: {e}")

    response_data = {
        "task_id": task_id,
        "student_id": student_id,
        "filename": filename,
        "file_path": save_path,
    }

    if parse_result and isinstance(parse_result, dict):
        response_data["status"] = parse_result.get("status", "completed")
        if "profile_json" in parse_result:
            response_data["block_count"] = parse_result.get("block_count", 0)
            response_data["message"] = "简历已上传并解析完成"
        else:
            response_data["message"] = "简历已上传，L0解析任务已创建"
    else:
        response_data["status"] = "pending"
        response_data["message"] = "简历已上传，L0解析任务已创建"

    return jsonify({
        "success": True,
        "data": response_data
    })


@profile_bp.route('/student/<student_id>', methods=['GET'])
def get_student_profile(student_id: str):
    """获取学生基本信息"""
    try:
        mysql = get_mysql_client()

        sql = """
        SELECT
            student_id, name, phone, email,
            degree, school, major, graduation_year,
            source_file, source_type, parse_version,
            completeness, created_at, updated_at
        FROM student_profile
        WHERE student_id = :student_id
        """

        results = mysql.execute(sql, {"student_id": student_id})

        if not results:
            return jsonify({
                "success": False,
                "error": f"未找到学生: {student_id}"
            }), 404

        profile = results[0]

        profile["phone"] = mask_phone(profile.get("phone", ""))

        profile['profile_json'] = parse_json_field(profile.get('profile_json'))

        return jsonify({
            "success": True,
            "data": profile
        })

    except Exception as e:
        logger.error(f"获取学生画像失败: {e}")
        return jsonify({
            "success": False,
            "error": "查询失败，请稍后重试",
            "code": ErrorCode.INTERNAL_ERROR.value
        }), 500


@profile_bp.route('/student/<student_id>/4d', methods=['GET'])
def get_student_4d(student_id: str):
    """获取学生四维评分详情 (D1-D4)"""
    try:
        mysql = get_mysql_client()

        target_job_id = request.args.get('target_job_id')

        if target_job_id:
            sql = """
            SELECT
                student_id, target_job_id,
                d1_score, d2_score, d3_score, d4_score,
                d5_score, d6_score, d7_score, d8_score, d9_score, d10_score,
                competitiveness, competitiveness_lvl, completeness,
                skill_coverage, skill_count, d3_confidence,
                portrait_json, created_at
            FROM student_portrait_4d
            WHERE student_id = :student_id AND target_job_id = :target_job_id
            """
            results = mysql.execute(sql, {"student_id": student_id, "target_job_id": target_job_id})
        else:
            sql = """
            SELECT
                student_id, target_job_id,
                d1_score, d2_score, d3_score, d4_score,
                d5_score, d6_score, d7_score, d8_score, d9_score, d10_score,
                competitiveness, competitiveness_lvl, completeness,
                skill_coverage, skill_count, d3_confidence,
                portrait_json, created_at
            FROM student_portrait_4d
            WHERE student_id = :student_id
            ORDER BY competitiveness DESC
            LIMIT 1
            """
            results = mysql.execute(sql, {"student_id": student_id})

        if not results:
            return jsonify({
                "success": False,
                "error": f"未找到学生画像: {student_id}"
            }), 404

        portrait = results[0]

        import json
        portrait['portrait_json'] = parse_json_field(portrait.get('portrait_json'))

        return jsonify({
            "success": True,
            "data": {
                "student_id": portrait['student_id'],
                "target_job_id": portrait.get('target_job_id'),
                "dimensions": build_dimensions_d1_d4(
                    portrait.get('d1_score', 0),
                    portrait.get('d2_score', 0),
                    portrait.get('d3_score', 0),
                    portrait.get('d4_score', 0),
                ),
                "sub_dimensions": build_sub_dimensions(
                    portrait.get('d2_score', 0),
                    portrait.get('d5_score') or 0,
                    portrait.get('d6_score') or 0,
                    portrait.get('d7_score') or 0,
                    portrait.get('d8_score') or 0,
                    portrait.get('d9_score') or 0,
                    portrait.get('d10_score') or 0,
                ),
                "total_score": portrait.get('competitiveness', 0),
                "level": portrait.get('competitiveness_lvl'),
                "completeness": portrait.get('completeness', 0),
                "skill_coverage": portrait.get('skill_coverage'),
                "skill_count": portrait.get('skill_count', 0),
                "generated_at": portrait.get('created_at')
            }
        })

    except Exception as e:
        return jsonify(safe_error("获取学生四维评分", e)), 500


@profile_bp.route('/student/<student_id>/basic', methods=['GET'])
def get_student_basic(student_id: str):
    """获取学生基础信息 (不含JSON)"""
    try:
        mysql = get_mysql_client()

        sql = """
        SELECT
            student_id, name, phone, email,
            degree, school, major, graduation_year,
            completeness, created_at
        FROM student_profile
        WHERE student_id = :student_id
        """

        results = mysql.execute(sql, {"student_id": student_id})

        if not results:
            return jsonify({
                "success": False,
                "error": f"未找到学生: {student_id}"
            }), 404

        return jsonify({
            "success": True,
            "data": results[0]
        })

    except Exception as e:
        logger.error(f"获取学生基础信息失败: {e}")
        return jsonify({
            "success": False,
            "error": "查询失败，请稍后重试",
            "code": ErrorCode.INTERNAL_ERROR.value
        }), 500


@profile_bp.route('/job/list', methods=['GET'])
def list_job_portraits():
    """获取岗位画像列表"""
    try:
        mysql = get_mysql_client()

        category = request.args.get('category')
        limit = request.args.get('limit', 20, type=int)
        offset = request.args.get('offset', 0, type=int)

        if category:
            sql = """
            SELECT
                portrait_id, position_name, jd_count,
                required_degree, required_skill_cnt,
                d1_weight, d2_weight, d3_weight, d4_weight,
                d5_weight, d6_weight, d7_weight, d8_weight, d9_weight, d10_weight,
                created_at
            FROM job_portrait_4d
            WHERE position_name LIKE :category
            ORDER BY jd_count DESC
            LIMIT :limit OFFSET :offset
            """
            results = mysql.execute(sql, {
                "category": f"%{category}%",
                "limit": limit,
                "offset": offset
            })

            count_sql = "SELECT COUNT(*) as total FROM job_portrait_4d WHERE position_name LIKE :category"
            count_result = mysql.execute(count_sql, {"category": f"%{category}%"})
            total = count_result[0]['total'] if count_result else 0
        else:
            sql = """
            SELECT
                portrait_id, position_name, jd_count,
                required_degree, required_skill_cnt,
                d1_weight, d2_weight, d3_weight, d4_weight,
                d5_weight, d6_weight, d7_weight, d8_weight, d9_weight, d10_weight,
                created_at
            FROM job_portrait_4d
            ORDER BY jd_count DESC
            LIMIT :limit OFFSET :offset
            """
            results = mysql.execute(sql, {"limit": limit, "offset": offset})

            count_sql = "SELECT COUNT(*) as total FROM job_portrait_4d"
            count_result = mysql.execute(count_sql)
            total = count_result[0]['total'] if count_result else 0

        return jsonify({
            "success": True,
            "data": results,
            "count": len(results),
            "total": total,
            "limit": limit,
            "offset": offset
        })

    except Exception as e:
        logger.error(f"获取岗位画像列表失败: {e}")
        return jsonify({
            "success": False,
            "error": "查询失败，请稍后重试",
            "code": ErrorCode.INTERNAL_ERROR.value
        }), 500


@profile_bp.route('/job/<portrait_id>', methods=['GET'])
def get_job_portrait(portrait_id: str):
    """获取岗位四维需求画像"""
    try:
        mysql = get_mysql_client()

        sql = """
        SELECT
            portrait_id, position_name, jd_count,
            required_degree, major_strict, required_skill_cnt,
            d1_weight, d2_weight, d3_weight, d4_weight,
            d5_weight, d6_weight, d7_weight, d8_weight, d9_weight, d10_weight,
            portrait_json, created_at
        FROM job_portrait_4d
        WHERE portrait_id = :portrait_id
        """

        results = mysql.execute(sql, {"portrait_id": portrait_id})

        if not results:
            return jsonify({
                "success": False,
                "error": f"未找到岗位画像: {portrait_id}"
            }), 404

        portrait = results[0]

        import json
        portrait['portrait_json'] = parse_json_field(portrait.get('portrait_json'))

        return jsonify({
            "success": True,
            "data": portrait
        })

    except Exception as e:
        logger.error(f"获取岗位画像失败: {e}")
        return jsonify({
            "success": False,
            "error": "查询失败，请稍后重试",
            "code": ErrorCode.INTERNAL_ERROR.value
        }), 500


@profile_bp.route('/job/search', methods=['GET'])
def search_jobs():
    """搜索岗位 (按名称/公司)"""
    try:
        mysql = get_mysql_client()

        keyword = request.args.get('keyword', '')
        limit = request.args.get('limit', 20, type=int)

        if not keyword:
            return jsonify({
                "success": False,
                "error": "keyword 不能为空"
            }), 400

        sql = """
        SELECT
            job_id, position_name, position_normalized,
            company_name, company_industry,
            degree_requirement, experience_min,
            salary_min, salary_max,
            is_it_position,
            created_at
        FROM job_posting
        WHERE (position_name LIKE :keyword OR company_name LIKE :keyword)
              AND is_it_position = 1
        ORDER BY created_at DESC
        LIMIT :limit
        """

        results = mysql.execute(sql, {
            "keyword": f"%{keyword}%",
            "limit": limit
        })

        return jsonify({
            "success": True,
            "data": results,
            "count": len(results)
        })

    except Exception as e:
        logger.error(f"搜索岗位失败: {e}")
        return jsonify({
            "success": False,
            "error": "查询失败，请稍后重试",
            "code": ErrorCode.INTERNAL_ERROR.value
        }), 500


@profile_bp.route('/job/categories', methods=['GET'])
def get_job_categories():
    """获取岗位分类统计"""
    try:
        mysql = get_mysql_client()

        sql = """
        SELECT
            job_category,
            COUNT(*) as job_count,
            AVG(salary_min) as avg_salary_min,
            AVG(salary_max) as avg_salary_max
        FROM job_posting
        WHERE job_category IS NOT NULL
        GROUP BY job_category
        ORDER BY job_count DESC
        """

        results = mysql.execute(sql)

        return jsonify({
            "success": True,
            "data": results,
            "count": len(results)
        })

    except Exception as e:
        logger.error(f"获取岗位分类失败: {e}")
        return jsonify({
            "success": False,
            "error": "查询失败，请稍后重试",
            "code": ErrorCode.INTERNAL_ERROR.value
        }), 500
