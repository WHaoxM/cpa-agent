"""
画像相关 API 路由
/api/profile — 学生画像、岗位画像的 CRUD 和生成
"""

import uuid
import re
from flask import request, jsonify

from . import profile_bp
from ._helpers import (
    ScopeError,
    build_dimensions_d1_d4,
    clamp_pagination,
    parse_json_field,
    require_owner,
    require_student_path,
    resolve_request_scope,
    safe_error,
    scope_error_response,
    sub_dimensions_from_payload,
)
from ..services.db import get_mysql_client
from ..utils.logger import get_logger

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
    session_id = request.form.get('session_id')

    try:
        scope = resolve_request_scope(session_id=session_id)
    except ScopeError as e:
        return scope_error_response(e)
    student_id = scope.student_id
    if session_id:
        try:
            from ..services.session_manager import get_session_manager
            session = get_session_manager().get_session(session_id)
            if not session:
                return jsonify({"success": False, "error": "会话不存在"}), 404
            require_owner(session.student_id, scope, resource="会话")
        except ScopeError as e:
            return scope_error_response(e)

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

    logger.info(f"简历上传: student_id={student_id}, filename={filename}, saved={save_path}")

    # ── D-06: 创建 pipeline_task 记录 + 触发 L0 管线 ──
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
        # No durable task id is available to this route after an exception;
        # do not invent a pending task that clients can never poll.
        return jsonify({"success": False, "error": "简历已保存，但解析未完成，请重试"}), 500

    # ── D-02: 回写 student_id 到 session ──
    if session_id:
        try:
            from ..services.session_manager import get_session_manager
            session_mgr = get_session_manager()
            session = session_mgr.get_session(session_id)
            if session and session.student_id == student_id:
                session_mgr.update_session(session_id, student_id=student_id)
        except Exception as e:
            logger.warning(f"Session student_id 回写失败: {e}")

    response_data = {
        "task_id": parse_result["task_id"],
        "student_id": student_id,
        "filename": filename,
    }

    response_data["status"] = parse_result.get("status", "unverified")
    response_data["block_count"] = parse_result.get("block_count", 0)
    response_data["message"] = parse_result.get("summary", "简历已上传，处理结果待核验")
    for key in ("candidate_profile", "structure_validation", "source_evidence", "profile_ready", "next_action"):
        response_data[key] = parse_result.get(key)

    return jsonify({
        "success": True,
        "data": response_data
    })


@profile_bp.route('/student/candidate/<task_id>', methods=['GET'])
def get_student_resume_candidate(task_id: str):
    """Replay an owned L0 candidate or its *current* confirmed workflow.

    A completed L0 task is readable only while it is the source of the current
    confirmed profile.  The linked L2 state comes from a persisted task whose
    result names that exact L0 confirmation, never from a page-local guess.
    """
    try:
        scope = resolve_request_scope()
        mysql = get_mysql_client()
        rows = mysql.fetch_all(
            "SELECT task_id, student_id, pipeline, status, result_json FROM pipeline_task WHERE task_id=:task_id LIMIT 1",
            {"task_id": task_id})
        if not rows or rows[0].get("student_id") != scope.student_id:
            return jsonify({"success": False, "error": "简历候选不存在"}), 404
        row = rows[0]
        if row.get("pipeline") != "L0_parse_resume" or row.get("status") not in {"needs_input", "partial", "completed"}:
            return jsonify({"success": False, "error": "任务不是待确认简历候选"}), 409
        result = parse_json_field(row.get("result_json"))
        result = result if isinstance(result, dict) else {}
        if result.get("task_id") not in (None, task_id) or result.get("student_id") not in (None, scope.student_id):
            return jsonify({"success": False, "error": "简历任务结果与权威记录不一致"}), 409
        public = {key: result.get(key) for key in (
            "candidate_profile", "structure_validation", "source_evidence", "profile_ready",
            "next_action", "block_count", "summary")}
        public["portrait_task"] = None
        public["confirmed"] = False
        if row["status"] == "completed":
            if result.get("confirmed") is not True or result.get("status") != "completed":
                return jsonify({"success": False, "error": "简历确认状态未通过校验"}), 409
            from ..services.pipeline import require_confirmed_profile
            try:
                profile = require_confirmed_profile(mysql, scope.student_id)
            except ValueError:
                return jsonify({"success": False, "error": "当前确认档案与简历任务不一致"}), 409
            if profile.get("_resume_confirmation", {}).get("task_id") != task_id:
                return jsonify({"success": False, "error": "此简历候选已被后续确认替代"}), 409
            public["confirmed"] = True
            public["profile_ready"] = True
            linked = mysql.fetch_all(
                "SELECT task_id, student_id, pipeline, status, result_json FROM pipeline_task "
                "WHERE student_id=:student_id AND pipeline='L2_generate_portrait' "
                "AND JSON_UNQUOTE(JSON_EXTRACT(result_json, '$.source_confirmation_task_id'))=:task_id "
                "ORDER BY id DESC LIMIT 1",
                {"student_id": scope.student_id, "task_id": task_id})
            if linked:
                portrait_row = linked[0]
                portrait = parse_json_field(portrait_row.get("result_json"))
                portrait = portrait if isinstance(portrait, dict) else {}
                if (portrait_row.get("student_id") == scope.student_id
                        and portrait_row.get("pipeline") == "L2_generate_portrait"
                        and portrait.get("source_confirmation_task_id") == task_id
                        and portrait.get("status") == portrait_row.get("status")):
                    if portrait_row["status"] == "completed":
                        from ..services.agent_runtime.pipeline_reference import resolve_pipeline_task_ref
                        try:
                            resolve_pipeline_task_ref(mysql, student_id=scope.student_id,
                                                      task_id=portrait_row["task_id"])
                        except (LookupError, ValueError):
                            return jsonify({"success": False, "error": "画像成果未通过权威记录校验"}), 409
                    public["portrait_task"] = {"task_id": portrait_row["task_id"],
                        "status": portrait_row["status"],
                        **{key: portrait.get(key) for key in (
                            "next_action", "summary", "missing_fields", "portrait_candidate",
                            "portrait_id", "portrait", "resource_type", "resource_id",
                            "action_available", "resume_state")}}
                    if portrait_row["status"] == "partial":
                        # Historical L2 rows predate these explicit capability
                        # fields; the semantic-fact writer is still unavailable.
                        public["portrait_task"]["action_available"] = False
                        public["portrait_task"]["resume_state"] = "not_available"
        public.update({"task_id": task_id, "student_id": scope.student_id,
                       "status": row["status"], "filename": "简历候选"})
        return jsonify({"success": True, "data": public})
    except ScopeError as e:
        return scope_error_response(e)
    except Exception as e:
        return jsonify(safe_error("读取简历候选", e)), 500


@profile_bp.route('/student/confirm', methods=['POST'])
def confirm_student_resume():
    """User confirmation boundary; Agent params cannot synthesize this consent."""
    try:
        scope = resolve_request_scope()
        data = request.get_json(silent=True) or {}
        if data.get("confirmed") is not True or not isinstance(data.get("task_id"), str):
            return jsonify({"success": False, "error": "需要 task_id 和明确 confirmed=true"}), 400
        from ..services.agent_runtime.resume_confirmation import run_confirmation
        result = run_confirmation(student_id=scope.student_id,
            task_id=data["task_id"], confirmed=True,
            acknowledge_partial=data.get("acknowledge_partial") is True)
        return jsonify({"success": True, "data": result})
    except ScopeError as e:
        return scope_error_response(e)
    except LookupError as e:
        return jsonify({"success": False, "error": str(e)}), 404
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 409
    except Exception as e:
        return jsonify(safe_error("确认简历", e)), 500


@profile_bp.route('/student/pipeline', methods=['POST'])
def run_confirmed_student_pipeline():
    """Page adapter to the same PipelineRunner used by the main Agent."""
    try:
        scope = resolve_request_scope()
        data = request.get_json(silent=True) or {}
        level = data.get("pipeline")
        if level not in {"L2_generate_portrait", "L3_run_match", "L4_generate_report"}:
            return jsonify({"success": False, "error": "不支持的管线"}), 400
        # No caller-written profile, scores, skills or confirmation flags.
        params = {k: data[k] for k in ("job_id", "job_portrait_id", "title") if data.get(k)}
        params["student_id"] = scope.student_id
        from ..services.pipeline import PipelineRunner
        runner = PipelineRunner()
        result = runner.run(level, params)
        tasks = runner.mysql.fetch_all(
            "SELECT student_id, status FROM pipeline_task WHERE task_id=:task_id LIMIT 1",
            {"task_id": result["task_id"]})
        if not tasks or tasks[0].get("student_id") != scope.student_id or tasks[0].get("status") != result.get("status"):
            raise RuntimeError("管线状态未通过权威记录回读验证")
        # A persisted partial is a successful request with an incomplete
        # business outcome, not an unsupported server operation. The page
        # must branch on data.status rather than treating it as a transport
        # failure or painting an invented completed artifact.
        if result.get("status") in {"completed", "partial", "needs_input"}:
            return jsonify({"success": True, "status": result["status"], "data": result})
        return jsonify({"success": False, "status": result.get("status"), "data": result,
                        "error": "管线未产生可展示的业务状态"}), 409
    except ScopeError as e:
        return scope_error_response(e)
    except ValueError as e:
        return jsonify({"success": False, "error": str(e)}), 409
    except Exception as e:
        return jsonify(safe_error("执行已确认简历管线", e)), 500


@profile_bp.route('/student/<student_id>', methods=['GET'])
def get_student_profile(student_id: str):
    """获取学生基本信息"""
    try:
        scope = resolve_request_scope()
        require_student_path(student_id, scope)
        mysql = get_mysql_client()

        sql = """
        SELECT
            student_id, name, phone, email,
            degree, school, major, graduation_year,
            source_file, source_type, parse_version,
            completeness, profile_json, created_at, updated_at
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

    except ScopeError as e:
        return scope_error_response(e)
    except Exception as e:
        return jsonify(safe_error("获取学生画像", e, message="查询失败，请稍后重试")), 500


@profile_bp.route('/student/<student_id>/4d', methods=['GET'])
def get_student_4d(student_id: str):
    """获取学生四维评分详情 (D1-D4)"""
    try:
        scope = resolve_request_scope()
        require_student_path(student_id, scope)
        mysql = get_mysql_client()

        target_job_id = request.args.get('target_job_id')

        if target_job_id:
            sql = """
            SELECT
                sp.student_id, sp.target_job_id,
                sp.d1_score, sp.d2_score, sp.d3_score, sp.d4_score,
                sp.competitiveness, sp.competitiveness_lvl, sp.completeness,
                sp.skill_coverage, sp.skill_count, sp.d3_confidence,
                sp.portrait_json, sp.created_at
            FROM student_portrait_4d sp
            JOIN student_profile p ON p.student_id = sp.student_id
            WHERE sp.student_id = :student_id AND sp.target_job_id = :target_job_id
            """
            results = mysql.execute(sql, {"student_id": student_id, "target_job_id": target_job_id})
        else:
            sql = """
            SELECT
                sp.student_id, sp.target_job_id,
                sp.d1_score, sp.d2_score, sp.d3_score, sp.d4_score,
                sp.competitiveness, sp.competitiveness_lvl, sp.completeness,
                sp.skill_coverage, sp.skill_count, sp.d3_confidence,
                sp.portrait_json, sp.created_at
            FROM student_portrait_4d sp
            JOIN student_profile p ON p.student_id = sp.student_id
            WHERE sp.student_id = :student_id
            ORDER BY sp.competitiveness DESC
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
                "sub_dimensions": sub_dimensions_from_payload(
                    portrait.get('d2_score', 0),
                    portrait.get('portrait_json'),
                ),
                "total_score": portrait.get('competitiveness', 0),
                "level": portrait.get('competitiveness_lvl'),
                "completeness": portrait.get('completeness', 0),
                "skill_coverage": portrait.get('skill_coverage'),
                "skill_count": portrait.get('skill_count', 0),
                "generated_at": portrait.get('created_at')
            }
        })

    except ScopeError as e:
        return scope_error_response(e)
    except Exception as e:
        return jsonify(safe_error("获取学生四维评分", e)), 500


@profile_bp.route('/student/<student_id>/basic', methods=['GET'])
def get_student_basic(student_id: str):
    """获取学生基础信息 (不含JSON)"""
    try:
        scope = resolve_request_scope()
        require_student_path(student_id, scope)
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

    except ScopeError as e:
        return scope_error_response(e)
    except Exception as e:
        return jsonify(safe_error("获取学生基础信息", e, message="查询失败，请稍后重试")), 500


@profile_bp.route('/student/<student_id>', methods=['PATCH', 'PUT'])
def patch_student_profile(student_id: str):
    """更新账号资料（user_account），可选同步 name/email/phone 到 student_profile 列。"""
    try:
        scope = resolve_request_scope()
        require_student_path(student_id, scope)
        data = request.get_json() or {}
        # whitelist only
        name = data.get("name") if "name" in data else data.get("nickname")
        email = data.get("email")
        phone = data.get("phone")
        signature = data.get("signature")
        avatar = data.get("avatar")
        sync_profile = bool(data.get("sync_student_profile", True))

        mysql = get_mysql_client()
        account = mysql.execute(
            "SELECT id FROM user_account WHERE id = :id LIMIT 1",
            {"id": student_id},
        )
        if not account:
            return jsonify({"success": False, "error": f"account not found: {student_id}"}), 404

        sets = []
        params: dict = {"id": student_id}
        if name is not None:
            sets.append("name = :name")
            params["name"] = name
        if email is not None:
            sets.append("email = :email")
            params["email"] = email
        if phone is not None:
            sets.append("phone = :phone")
            params["phone"] = phone
        if signature is not None:
            sets.append("signature = :signature")
            params["signature"] = signature
        if avatar is not None:
            sets.append("avatar = :avatar")
            params["avatar"] = avatar

        if not sets:
            return jsonify({"success": False, "error": "no updatable fields"}), 400

        mysql.execute_write(
            f"UPDATE user_account SET {', '.join(sets)}, updated_at = NOW() WHERE id = :id",
            params,
        )

        synced = False
        if sync_profile and any(k in params for k in ("name", "email", "phone")):
            profile_row = mysql.execute(
                "SELECT student_id FROM student_profile WHERE student_id = :id LIMIT 1",
                {"id": student_id},
            )
            if profile_row:
                psets = []
                pparams: dict = {"id": student_id}
                if "name" in params:
                    psets.append("name = :name")
                    pparams["name"] = params["name"]
                if "email" in params:
                    psets.append("email = :email")
                    pparams["email"] = params["email"]
                if "phone" in params:
                    psets.append("phone = :phone")
                    pparams["phone"] = params["phone"]
                if psets:
                    mysql.execute_write(
                        f"UPDATE student_profile SET {', '.join(psets)}, updated_at = NOW() "
                        f"WHERE student_id = :id",
                        pparams,
                    )
                    synced = True

        row = mysql.execute(
            "SELECT id, name, email, phone, avatar, signature, role, updated_at "
            "FROM user_account WHERE id = :id",
            {"id": student_id},
        )[0]
        return jsonify({
            "success": True,
            "data": {**row, "synced_student_profile": synced},
        })
    except ScopeError as e:
        return scope_error_response(e)
    except Exception as e:
        return jsonify(safe_error("更新学生资料", e, message="更新失败，请稍后重试")), 500


@profile_bp.route('/job/list', methods=['GET'])
def list_job_portraits():
    """获取岗位画像列表"""
    try:
        mysql = get_mysql_client()

        category = request.args.get('category')
        limit, offset = clamp_pagination(
            request.args.get('limit', type=int),
            request.args.get('offset', type=int),
            default_limit=20,
        )

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
        return jsonify(safe_error("获取岗位画像列表", e, message="查询失败，请稍后重试")), 500


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
        portrait['portrait_json'] = parse_json_field(portrait.get('portrait_json'))

        return jsonify({
            "success": True,
            "data": portrait
        })

    except Exception as e:
        return jsonify(safe_error("获取岗位画像", e, message="查询失败，请稍后重试")), 500


@profile_bp.route('/job/search', methods=['GET'])
def search_jobs():
    """搜索岗位 (按名称/公司)"""
    try:
        mysql = get_mysql_client()

        keyword = request.args.get('keyword', '')
        limit, _ = clamp_pagination(request.args.get('limit', type=int), None, default_limit=20)

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
        return jsonify(safe_error("搜索岗位", e, message="查询失败，请稍后重试")), 500


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
        return jsonify(safe_error("获取岗位分类", e, message="查询失败，请稍后重试")), 500
