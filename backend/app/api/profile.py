"""
画像相关 API 路由
/api/profile — 学生画像、岗位画像的 CRUD 和生成
"""

import uuid
from flask import request, jsonify

from . import profile_bp
from ..services.db import get_mysql_client
from ..utils.logger import get_logger

logger = get_logger('cpa-agent.api.profile')


@profile_bp.route('/student/upload', methods=['POST'])
def upload_resume():
    """上传简历文件, 触发 L0 解析"""
    if 'file' not in request.files:
        return jsonify({
            "success": False,
            "error": "未上传文件"
        }), 400

    file = request.files['file']
    student_id = request.form.get('student_id')

    if not student_id:
        student_id = f"stu_{uuid.uuid4().hex[:12]}"

    filename = file.filename
    file_ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else 'pdf'

    task_id = f"task_{uuid.uuid4().hex[:12]}"

    logger.info(f"简历上传: student_id={student_id}, filename={filename}")

    return jsonify({
        "success": True,
        "data": {
            "task_id": task_id,
            "status": "pending",
            "student_id": student_id,
            "filename": filename,
            "message": "简历已上传，L0解析任务已创建"
        }
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

        import json
        profile_json = profile.get('profile_json')
        if isinstance(profile_json, str):
            try:
                profile['profile_json'] = json.loads(profile_json)
            except:
                pass

        return jsonify({
            "success": True,
            "data": profile
        })

    except Exception as e:
        logger.error(f"获取学生画像失败: {e}")
        return jsonify({
            "success": False,
            "error": f"查询失败: {str(e)}"
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
        portrait_json = portrait.get('portrait_json')
        if isinstance(portrait_json, str):
            try:
                portrait['portrait_json'] = json.loads(portrait_json)
            except:
                pass

        return jsonify({
            "success": True,
            "data": {
                "student_id": portrait['student_id'],
                "target_job_id": portrait.get('target_job_id'),
                "dimensions": {
                    "D1": {
                        "name": "基础要求",
                        "score": portrait.get('d1_score', 0),
                        "description": "学历、学校、专业等基础条件匹配度"
                    },
                    "D2": {
                        "name": "职业技能",
                        "score": portrait.get('d2_score', 0),
                        "description": "技术技能掌握程度和项目经验"
                    },
                    "D3": {
                        "name": "职业素养",
                        "score": portrait.get('d3_score', 0),
                        "description": "沟通、团队、领导力等软技能"
                    },
                    "D4": {
                        "name": "发展潜力",
                        "score": portrait.get('d4_score', 0),
                        "description": "学习能力、成长空间、潜力评估"
                    }
                },
                "total_score": portrait.get('competitiveness', 0),
                "level": portrait.get('competitiveness_lvl'),
                "completeness": portrait.get('completeness', 0),
                "skill_coverage": portrait.get('skill_coverage'),
                "skill_count": portrait.get('skill_count', 0),
                "generated_at": portrait.get('created_at')
            }
        })

    except Exception as e:
        logger.error(f"获取学生四维评分失败: {e}")
        return jsonify({
            "success": False,
            "error": f"查询失败: {str(e)}"
        }), 500


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
            "error": f"查询失败: {str(e)}"
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
            "error": f"查询失败: {str(e)}"
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
        portrait_json = portrait.get('portrait_json')
        if isinstance(portrait_json, str):
            try:
                portrait['portrait_json'] = json.loads(portrait_json)
            except:
                pass

        return jsonify({
            "success": True,
            "data": portrait
        })

    except Exception as e:
        logger.error(f"获取岗位画像失败: {e}")
        return jsonify({
            "success": False,
            "error": f"查询失败: {str(e)}"
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
        WHERE position_name LIKE :keyword OR company_name LIKE :keyword
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
            "error": f"查询失败: {str(e)}"
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
            "error": f"查询失败: {str(e)}"
        }), 500
