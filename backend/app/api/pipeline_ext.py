"""
Pipeline API - 实体抽取管线 API
封装 L0/L1 技能抽取、人岗匹配、技能推荐

与 pipeline.py 的区别:
- pipeline.py: 异步任务队列模式，提供 /trigger、/status 等任务管理端点
- 本模块: 同步细粒度 API，提供 /extract、/match、/portrait 等直接调用端点
- 适用于需要立即获取结果的场景，无需等待异步任务完成

结构分区 (P1 拆分进行中, 逐步迁出至 pipeline_ext_*.py 子模块):
- L0+L1: 文本解析与技能抽取
- L3: 匹配结果存储
- L2: 画像存储与查询
- L4: 报告列表与状态
- Agent: 对话历史与统计 (含混杂路由)
- Normalizer: 实体归一化 (已拆出 pipeline_ext_normalizer.py)
- 技能自评 (已拆出 pipeline_ext_assessment.py)
- L0 文本解析 (已拆出 pipeline_ext_parse_text.py)
- Milvus 向量 (已拆出 pipeline_ext_vector.py, 7 路由)
- L0 文件上传 (已拆出 pipeline_ext_upload.py, 2 路由)
- L1 图谱+JD聚合 (已拆出 pipeline_ext_graph.py, 10 路由)

公共对象 (pipeline_ext_bp / logger / _normalize_skills) 已迁至 pipeline_ext_common.py。
"""

import uuid
from flask import request, jsonify

from .pipeline_ext_common import pipeline_ext_bp, logger, _normalize_skills
from ..exceptions import ErrorCode


@pipeline_ext_bp.route('/extract', methods=['POST'])
def extract_skills():
    """
    L0+L1: 技能抽取 + 图谱存储

    Body:
        {
            "text": "简历或JD文本",
            "entity_id": "可选，实体ID",
            "entity_type": "student|job",
            "use_llm": true|false
        }
    """
    data = request.get_json() or {}
    text = data.get('text', '')
    entity_id = data.get('entity_id')
    entity_type = data.get('entity_type', 'student')
    use_llm = data.get('use_llm', True)

    if not text:
        return jsonify({
            "success": False,
            "error": "text 不能为空"
        }), 400

    try:
        from ..pipeline.entity_extractor.entity_pipeline import EntityPipeline
        pipeline = EntityPipeline(use_llm=use_llm)
        result = pipeline.run_full(text, entity_id)

        if result["success"]:
            return jsonify({
                "success": True,
                "data": {
                    "entity_id": result["data"]["entity_id"],
                    "skills": result["data"]["l1"]["skills"],
                    "relations": result["data"]["l1"]["relations"],
                    "skill_count": result["data"]["summary"]["skills_count"],
                    "categories": result["data"]["summary"]["categories"]
                }
            })
        else:
            return jsonify({
                "success": False,
                "error": result.get("error", "抽取失败")
            }), 500

    except Exception as e:
        logger.error(f"技能抽取失败: {e}")
        return jsonify({
            "success": False,
            "error": "服务器内部错误", "code": ErrorCode.INTERNAL_ERROR.value
        }), 500


@pipeline_ext_bp.route('/extract/skills', methods=['POST'])
def extract_skills_only():
    """
    仅抽取技能（不存图谱）

    Body:
        {"text": "文本", "use_llm": true|false}
    """
    data = request.get_json() or {}
    text = data.get('text', '')
    use_llm = data.get('use_llm', True)

    if not text:
        return jsonify({"success": False, "error": "text 不能为空"}), 400

    try:
        from ..pipeline.entity_extractor.entity_pipeline import EntityPipeline
        pipeline = EntityPipeline(use_llm=use_llm)
        result = pipeline.run_l1_extract(text)

        if result["success"]:
            return jsonify({
                "success": True,
                "data": {
                    "skills": result["data"]["skills"],
                    "skill_count": result["data"]["skill_count"]
                }
            })
        else:
            return jsonify({"success": False, "error": result.get("error")}), 500

    except Exception as e:
        logger.error(f"技能抽取失败: {e}")
        return jsonify({"success": False, "error": "服务器内部错误", "code": ErrorCode.INTERNAL_ERROR.value}), 500


@pipeline_ext_bp.route('/report/full', methods=['POST'])
def create_full_report():
    """
    L4: 生成完整报告（整合L2+L3+L4）

    Body:
        {
            "student_id": "stu_001",
            "student_skills": [...],
            "job_id": "job_001",
            "job_name": "Python后端开发",
            "job_skills": [...],
            "use_llm_summary": true
        }
    """
    data = request.get_json() or {}
    student_id = data.get('student_id', f'stu_{uuid.uuid4().hex[:8]}')
    student_skills = _normalize_skills(data.get('student_skills', []))
    job_id = data.get('job_id', f'job_{uuid.uuid4().hex[:8]}')
    job_name = data.get('job_name', '未知岗位')
    job_skills = _normalize_skills(data.get('job_skills', []))
    use_llm_summary = data.get('use_llm_summary', True)

    if not student_skills:
        return jsonify({"success": False, "error": "student_skills 不能为空"}), 400
    if not job_skills:
        return jsonify({"success": False, "error": "job_skills 不能为空"}), 400

    try:
        from ..pipeline.entity_extractor.l4_report_generator import generate_full_report
        report = generate_full_report(
            student_id, student_skills,
            job_id, job_name, job_skills,
            use_llm_summary=use_llm_summary
        )

        return jsonify({
            "success": True,
            "data": report
        })

    except Exception as e:
        logger.error(f"生成完整报告失败: {e}")
        return jsonify({"success": False, "error": "服务器内部错误", "code": ErrorCode.INTERNAL_ERROR.value}), 500


@pipeline_ext_bp.route('/report/html', methods=['POST'])
def export_report_html():
    """
    L4: 导出 HTML 报告

    Body:
        {
            "student_id": "stu_001",
            "student_skills": [...],
            "job_id": "job_001",
            "job_name": "Python后端开发",
            "job_skills": [...],
            "llm_summary": "可选的LLM摘要"
        }
    """
    data = request.get_json() or {}
    student_id = data.get('student_id')
    student_skills = _normalize_skills(data.get('student_skills', []))
    job_id = data.get('job_id')
    job_name = data.get('job_name', '未知岗位')
    job_skills = _normalize_skills(data.get('job_skills', []))
    llm_summary = data.get('llm_summary', '')

    if not student_skills or not job_skills:
        return jsonify({"success": False, "error": "skills 不能为空"}), 400

    try:
        from ..pipeline.entity_extractor.report_generator import generate_match_report

        report = generate_match_report(
            student_id or f'stu_{uuid.uuid4().hex[:8]}',
            student_skills,
            job_id or f'job_{uuid.uuid4().hex[:8]}',
            job_name,
            job_skills
        )

        report["student_skills"] = [s["name"] for s in student_skills]
        report["job_skills"] = [s["name"] for s in job_skills]

        from ..pipeline.entity_extractor.l4_report_generator import L4ReportGenerator
        generator = L4ReportGenerator()
        html = generator.generate_html(report, llm_summary)

        return jsonify({
            "success": True,
            "data": {
                "html": html,
                "content_type": "text/html"
            }
        })

    except Exception as e:
        logger.error(f"生成HTML报告失败: {e}")
        return jsonify({"success": False, "error": "服务器内部错误", "code": ErrorCode.INTERNAL_ERROR.value}), 500


@pipeline_ext_bp.route('/report/summary', methods=['POST'])
def generate_report_summary():
    """
    L4: 仅生成 LLM 摘要

    Body:
        报告数据
    """
    data = request.get_json() or {}

    if not data:
        return jsonify({"success": False, "error": "报告数据不能为空"}), 400

    try:
        from ..pipeline.entity_extractor.l4_report_generator import generate_llm_summary
        summary = generate_llm_summary(data)

        return jsonify({
            "success": True,
            "data": {
                "summary": summary
            }
        })

    except Exception as e:
        logger.error(f"生成摘要失败: {e}")
        return jsonify({"success": False, "error": "服务器内部错误", "code": ErrorCode.INTERNAL_ERROR.value}), 500


@pipeline_ext_bp.route('/match', methods=['POST'])
def match_student_job():
    """
    人岗匹配

    Body:
        {
            "student_skills": [{"name": "Python", "level": "掌握", "confidence": 0.8}],
            "job_skills": [{"name": "Python", "level": "必须", "weight": 0.9}]
        }
    """
    data = request.get_json() or {}
    student_skills = _normalize_skills(data.get('student_skills', []))
    job_skills = _normalize_skills(data.get('job_skills', []))

    if not student_skills:
        return jsonify({"success": False, "error": "student_skills 不能为空"}), 400
    if not job_skills:
        return jsonify({"success": False, "error": "job_skills 不能为空"}), 400

    try:
        from ..pipeline.entity_extractor.matcher import calculate_match_score
        result = calculate_match_score(student_skills, job_skills)

        return jsonify({
            "success": True,
            "data": result
        })

    except Exception as e:
        logger.error(f"人岗匹配失败: {e}")
        return jsonify({"success": False, "error": "服务器内部错误", "code": ErrorCode.INTERNAL_ERROR.value}), 500


@pipeline_ext_bp.route('/match/auto', methods=['POST'])
def auto_match():
    """
    自动人岗匹配（从文本抽取后匹配）

    Body:
        {
            "student_text": "学生简历文本",
            "job_text": "岗位描述文本"
        }
    """
    data = request.get_json() or {}
    student_text = data.get('student_text', '')
    job_text = data.get('job_text', '')

    if not student_text:
        return jsonify({"success": False, "error": "student_text 不能为空"}), 400
    if not job_text:
        return jsonify({"success": False, "error": "job_text 不能为空"}), 400

    try:
        from ..pipeline.entity_extractor.entity_pipeline import EntityPipeline
        from ..pipeline.entity_extractor.matcher import calculate_match_score
        student_pipeline = EntityPipeline(use_llm=True)
        job_pipeline = EntityPipeline(use_llm=True)

        student_result = student_pipeline.run_l1_extract(student_text, f"student_{uuid.uuid4().hex[:8]}")
        job_result = job_pipeline.run_l1_extract(job_text, f"job_{uuid.uuid4().hex[:8]}")

        if not student_result["success"]:
            return jsonify({"success": False, "error": "学生技能抽取失败"}), 500
        if not job_result["success"]:
            return jsonify({"success": False, "error": "岗位技能抽取失败"}), 500

        student_skills = student_result["data"]["skills"]
        job_skills = job_result["data"]["skills"]

        # ── 归一化: 技能名 → canonical 规范名 ──
        try:
            from ..services.entity_normalizer import get_entity_normalizer
            normalizer = get_entity_normalizer()
            student_skills = normalizer.normalize_batch(student_skills, "skill", student_text[:500])
            job_skills = normalizer.normalize_batch(job_skills, "skill", job_text[:500])
        except Exception as e:
            logger.warning(f"技能归一化降级: {e}")

        student_skills_input = [
            {"name": s["name"], "level": "掌握" if s.get("confidence", 0) > 0.6 else "熟悉", "confidence": s.get("confidence", 0.5)}
            for s in student_skills
        ]
        job_skills_input = [
            {"name": s["name"], "level": "必须", "weight": 0.8}
            for s in job_skills
        ]

        match_result = calculate_match_score(student_skills_input, job_skills_input)

        return jsonify({
            "success": True,
            "data": {
                "student_skills": student_skills,
                "job_skills": job_skills,
                "match": match_result
            }
        })

    except Exception as e:
        logger.error(f"自动匹配失败: {e}")
        return jsonify({"success": False, "error": "服务器内部错误", "code": ErrorCode.INTERNAL_ERROR.value}), 500


@pipeline_ext_bp.route('/recommend', methods=['GET'])
def recommend_skills():
    """
    技能推荐

    Query:
        ?skills=Python,Django,MySQL&limit=5
    """
    skills_str = request.args.get('skills', '')
    limit = request.args.get('limit', 5, type=int)

    if not skills_str:
        return jsonify({"success": False, "error": "skills 参数不能为空"}), 400

    skills = [s.strip() for s in skills_str.split(',') if s.strip()]

    try:
        from ..pipeline.entity_extractor.graph_storage import GraphStorage
        storage = GraphStorage()
        recommendations = storage.recommend_skills(skills, limit=limit)

        return jsonify({
            "success": True,
            "data": {
                "input_skills": skills,
                "recommendations": recommendations
            }
        })

    except Exception as e:
        logger.error(f"技能推荐失败: {e}")
        return jsonify({"success": False, "error": "服务器内部错误", "code": ErrorCode.INTERNAL_ERROR.value}), 500


@pipeline_ext_bp.route('/path', methods=['GET'])
def find_learning_path():
    """
    学习路径查找

    Query:
        ?from=Python&to=Kubernetes&max_hops=5
    """
    from_skill = request.args.get('from', '')
    to_skill = request.args.get('to', '')
    max_hops = request.args.get('max_hops', 5, type=int)

    if not from_skill:
        return jsonify({"success": False, "error": "from 参数不能为空"}), 400
    if not to_skill:
        return jsonify({"success": False, "error": "to 参数不能为空"}), 400

    try:
        from ..pipeline.entity_extractor.graph_storage import GraphStorage
        storage = GraphStorage()
        path = storage.find_learning_path(from_skill, to_skill, max_hops=max_hops)

        return jsonify({
            "success": True,
            "data": path
        })

    except Exception as e:
        logger.error(f"路径查找失败: {e}")
        return jsonify({"success": False, "error": "服务器内部错误", "code": ErrorCode.INTERNAL_ERROR.value}), 500


@pipeline_ext_bp.route('/graph', methods=['GET'])
def get_skill_graph():
    """
    获取技能子图

    Query:
        ?skills=Python,Django,MySQL
    """
    skills_str = request.args.get('skills', '')

    if not skills_str:
        return jsonify({"success": False, "error": "skills 参数不能为空"}), 400

    skills = [s.strip() for s in skills_str.split(',') if s.strip()]

    try:
        from ..pipeline.entity_extractor.graph_storage import GraphStorage
        storage = GraphStorage()
        graph = storage.get_skill_graph(skills)

        return jsonify({
            "success": True,
            "data": graph
        })

    except Exception as e:
        logger.error(f"获取技能图谱失败: {e}")
        return jsonify({"success": False, "error": "服务器内部错误", "code": ErrorCode.INTERNAL_ERROR.value}), 500


@pipeline_ext_bp.route('/portrait/student', methods=['POST'])
def create_student_portrait():
    """
    L2: 生成学生画像

    Body:
        {
            "student_id": "stu_001",
            "skills": [{"name": "Python", "category": "编程语言", "confidence": 0.85}],
            "target_job_id": "可选"
        }
    """
    data = request.get_json() or {}
    student_id = data.get('student_id', f'stu_{uuid.uuid4().hex[:8]}')
    skills = _normalize_skills(data.get('skills', []))
    target_job_id = data.get('target_job_id')

    if not skills:
        return jsonify({"success": False, "error": "skills 不能为空"}), 400

    try:
        from ..pipeline.entity_extractor.portrait_generator import generate_student_portrait
        portrait = generate_student_portrait(student_id, skills)

        return jsonify({
            "success": True,
            "data": portrait
        })

    except Exception as e:
        logger.error(f"生成学生画像失败: {e}")
        return jsonify({"success": False, "error": "服务器内部错误", "code": ErrorCode.INTERNAL_ERROR.value}), 500


@pipeline_ext_bp.route('/portrait/job', methods=['POST'])
def create_job_portrait():
    """
    L2: 生成岗位画像

    Body:
        {
            "job_id": "job_001",
            "position_name": "Python后端开发",
            "skills": [{"name": "Python", "category": "编程语言", "level": "必须"}],
            "salary_range": {"min": 15, "max": 30}
        }
    """
    data = request.get_json() or {}
    job_id = data.get('job_id', f'job_{uuid.uuid4().hex[:8]}')
    position_name = data.get('position_name', '未知岗位')
    skills = _normalize_skills(data.get('skills', []))
    salary_range = data.get('salary_range')

    try:
        from ..pipeline.entity_extractor.portrait_generator import generate_job_portrait
        portrait = generate_job_portrait(job_id, position_name, skills)

        return jsonify({
            "success": True,
            "data": portrait
        })

    except Exception as e:
        logger.error(f"生成岗位画像失败: {e}")
        return jsonify({"success": False, "error": "服务器内部错误", "code": ErrorCode.INTERNAL_ERROR.value}), 500


@pipeline_ext_bp.route('/report/match', methods=['POST'])
def create_match_report():
    """
    L3: 生成人岗匹配报告

    Body:
        {
            "student_id": "stu_001",
            "student_skills": [...],
            "job_id": "job_001",
            "job_name": "Python后端开发",
            "job_skills": [...]
        }
    """
    data = request.get_json() or {}
    student_id = data.get('student_id', f'stu_{uuid.uuid4().hex[:8]}')
    student_skills = _normalize_skills(data.get('student_skills', []))
    job_id = data.get('job_id', f'job_{uuid.uuid4().hex[:8]}')
    job_name = data.get('job_name', '未知岗位')
    job_skills = _normalize_skills(data.get('job_skills', []))

    if not student_skills:
        return jsonify({"success": False, "error": "student_skills 不能为空"}), 400
    if not job_skills:
        return jsonify({"success": False, "error": "job_skills 不能为空"}), 400

    try:
        from ..pipeline.entity_extractor.report_generator import generate_match_report
        report = generate_match_report(
            student_id, student_skills,
            job_id, job_name, job_skills
        )

        return jsonify({
            "success": True,
            "data": report
        })

    except Exception as e:
        logger.error(f"生成匹配报告失败: {e}")
        return jsonify({"success": False, "error": "服务器内部错误", "code": ErrorCode.INTERNAL_ERROR.value}), 500


@pipeline_ext_bp.route('/report/career', methods=['POST'])
def create_career_report():
    """
    L3: 生成职业发展报告

    Body:
        {
            "student_id": "stu_001",
            "skills": [...]
        }
    """
    data = request.get_json() or {}
    student_id = data.get('student_id', f'stu_{uuid.uuid4().hex[:8]}')
    skills = _normalize_skills(data.get('skills', []))

    if not skills:
        return jsonify({"success": False, "error": "skills 不能为空"}), 400

    try:
        from ..pipeline.entity_extractor.report_generator import generate_career_report
        report = generate_career_report(student_id, skills)

        return jsonify({
            "success": True,
            "data": report
        })

    except Exception as e:
        logger.error(f"生成职业报告失败: {e}")
        return jsonify({"success": False, "error": "服务器内部错误", "code": ErrorCode.INTERNAL_ERROR.value}), 500


@pipeline_ext_bp.route('/parse/resume', methods=['POST'])
def parse_resume_api():
    """
    L0: 解析简历文本

    Body:
        {
            "text": "简历文本内容",
            "extract_skills": true  // 是否顺便抽取技能
        }
    """
    data = request.get_json() or {}
    text = data.get('text', '')
    extract_skills = data.get('extract_skills', False)

    if not text:
        return jsonify({"success": False, "error": "text 不能为空"}), 400

    try:
        from ..pipeline.entity_extractor.l0_parser import parse_resume_text
        result = parse_resume_text(text)

        if not result.success:
            return jsonify({"success": False, "error": result.error}), 500

        response_data = {
            "text": result.text,
            "block_count": len(result.blocks),
            "blocks": result.blocks,
            "metadata": result.metadata
        }

        if extract_skills:
            from ..pipeline.entity_extractor.llm_skill_extractor import extract_skills_with_relations
            skills_result = extract_skills_with_relations(text, use_llm=True)
            response_data["skills"] = skills_result.get("skills", [])
            response_data["skill_count"] = len(skills_result.get("skills", []))

        return jsonify({
            "success": True,
            "data": response_data
        })

    except Exception as e:
        logger.error(f"简历解析失败: {e}")
        return jsonify({"success": False, "error": "服务器内部错误", "code": ErrorCode.INTERNAL_ERROR.value}), 500


@pipeline_ext_bp.route('/parse/jd', methods=['POST'])
def parse_jd_api():
    """
    L0: 解析职位描述文本

    Body:
        {
            "text": "JD文本内容",
            "extract_skills": true
        }
    """
    data = request.get_json() or {}
    text = data.get('text', '')
    extract_skills = data.get('extract_skills', False)

    if not text:
        return jsonify({"success": False, "error": "text 不能为空"}), 400

    try:
        from ..pipeline.entity_extractor.l0_parser import parse_jd_text
        result = parse_jd_text(text)

        if not result.success:
            return jsonify({"success": False, "error": result.error}), 500

        response_data = {
            "text": result.text,
            "block_count": len(result.blocks),
            "blocks": result.blocks,
            "metadata": result.metadata
        }

        if extract_skills:
            from ..pipeline.entity_extractor.llm_skill_extractor import extract_skills_with_relations
            skills_result = extract_skills_with_relations(text, use_llm=True)
            response_data["skills"] = skills_result.get("skills", [])
            response_data["skill_count"] = len(skills_result.get("skills", []))

        return jsonify({
            "success": True,
            "data": response_data
        })

    except Exception as e:
        logger.error(f"JD解析失败: {e}")
        return jsonify({"success": False, "error": "服务器内部错误", "code": ErrorCode.INTERNAL_ERROR.value}), 500


@pipeline_ext_bp.route('/parse/full', methods=['POST'])
def parse_full_api():
    """
    L0+L1: 完整解析（简历+技能抽取+图谱存储）

    Body:
        {
            "student_text": "简历文本",
            "job_text": "岗位文本",
            "student_id": "可选",
            "job_id": "可选"
        }
    """
    data = request.get_json() or {}
    student_text = data.get('student_text', '')
    job_text = data.get('job_text', '')
    student_id = data.get('student_id', f'stu_{uuid.uuid4().hex[:8]}')
    job_id = data.get('job_id', f'job_{uuid.uuid4().hex[:8]}')

    if not student_text and not job_text:
        return jsonify({"success": False, "error": "student_text 或 job_text 至少需要一个"}), 400

    try:
        result = {}

        if student_text:
            from ..pipeline.entity_extractor.l0_parser import parse_resume_text
            resume_result = parse_resume_text(student_text)
            result["resume"] = {
                "success": resume_result.success,
                "blocks": resume_result.blocks,
                "metadata": resume_result.metadata
            }

            if resume_result.success:
                from ..pipeline.entity_extractor.entity_pipeline import EntityPipeline
                pipeline = EntityPipeline(use_llm=True)
                l1_result = pipeline.run_l1_extract(student_text, student_id)
                result["resume"]["skills"] = l1_result.get("data", {}).get("skills", [])

        if job_text:
            from ..pipeline.entity_extractor.l0_parser import parse_jd_text
            jd_result = parse_jd_text(job_text)
            result["job"] = {
                "success": jd_result.success,
                "blocks": jd_result.blocks,
                "metadata": jd_result.metadata
            }

            if jd_result.success:
                from ..pipeline.entity_extractor.entity_pipeline import EntityPipeline
                pipeline = EntityPipeline(use_llm=True)
                l1_result = pipeline.run_l1_extract(job_text, job_id)
                result["job"]["skills"] = l1_result.get("data", {}).get("skills", [])

        return jsonify({
            "success": True,
            "data": result
        })

    except Exception as e:
        logger.error(f"完整解析失败: {e}")
        return jsonify({"success": False, "error": "服务器内部错误", "code": ErrorCode.INTERNAL_ERROR.value}), 500


@pipeline_ext_bp.route('/nsle/resume', methods=['POST'])
def nsle_resume_api():
    """
    NS-LE Pipeline: 简历完整解析 (Phase1-4)

    Body:
        {"text": "简历文本"}
    """
    data = request.get_json() or {}
    text = data.get('text', '')

    if not text:
        return jsonify({"success": False, "error": "text 不能为空"}), 400

    try:
        from ..pipeline.entity_extractor.nsle_pipeline import run_nsle_resume
        result = run_nsle_resume(text)

        if not result.get("success"):
            return jsonify({"success": False, "error": result.get("error", "解析失败")}), 500

        return jsonify({
            "success": True,
            "data": {
                "blocks": [
                    {
                        "block_id": b.block_id,
                        "block_type": b.block_type,
                        "text": b.text[:200],
                        "confidence": b.confidence
                    }
                    for b in result.get("blocks", [])
                ],
                "skills": result.get("skills", []),
                "entities": result.get("entities", {})
            }
        })

    except Exception as e:
        logger.error(f"NS-LE简历解析失败: {e}")
        return jsonify({"success": False, "error": "服务器内部错误", "code": ErrorCode.INTERNAL_ERROR.value}), 500


@pipeline_ext_bp.route('/nsle/jd', methods=['POST'])
def nsle_jd_api():
    """
    NS-LE Pipeline: JD完整解析 (Phase1-4)

    Body:
        {"text": "JD文本"}
    """
    data = request.get_json() or {}
    text = data.get('text', '')

    if not text:
        return jsonify({"success": False, "error": "text 不能为空"}), 400

    try:
        from ..pipeline.entity_extractor.nsle_pipeline import run_nsle_jd
        result = run_nsle_jd(text)

        if not result.get("success"):
            return jsonify({"success": False, "error": result.get("error", "解析失败")}), 500

        return jsonify({
            "success": True,
            "data": {
                "blocks": [
                    {
                        "block_id": b.block_id,
                        "block_type": b.block_type,
                        "text": b.text[:200],
                        "confidence": b.confidence
                    }
                    for b in result.get("blocks", [])
                ],
                "required_skills": result.get("required_skills", []),
                "entities": result.get("entities", {})
            }
        })

    except Exception as e:
        logger.error(f"NS-LE JD解析失败: {e}")
        return jsonify({"success": False, "error": "服务器内部错误", "code": ErrorCode.INTERNAL_ERROR.value}), 500


@pipeline_ext_bp.route('/nsle/full', methods=['POST'])
def nsle_full_api():
    """
    NS-LE Pipeline: 简历+JD完整解析

    Body:
        {
            "resume_text": "简历文本",
            "job_text": "JD文本"
        }
    """
    data = request.get_json() or {}
    resume_text = data.get('resume_text', '')
    job_text = data.get('job_text', '')

    if not resume_text and not job_text:
        return jsonify({"success": False, "error": "resume_text 或 job_text 至少需要一个"}), 400

    try:
        from ..pipeline.entity_extractor.nsle_pipeline import run_nsle_resume, run_nsle_jd
        result = {}

        if resume_text:
            resume_result = run_nsle_resume(resume_text)
            result["resume"] = {
                "success": resume_result.get("success", False),
                "blocks": [
                    {
                        "block_id": b.block_id,
                        "block_type": b.block_type,
                        "text": b.text[:100],
                        "confidence": b.confidence
                    }
                    for b in resume_result.get("blocks", [])
                ],
                "skills": resume_result.get("skills", [])
            }

        if job_text:
            jd_result = run_nsle_jd(job_text)
            result["job"] = {
                "success": jd_result.get("success", False),
                "blocks": [
                    {
                        "block_id": b.block_id,
                        "block_type": b.block_type,
                        "text": b.text[:100],
                        "confidence": b.confidence
                    }
                    for b in jd_result.get("blocks", [])
                ],
                "required_skills": jd_result.get("required_skills", [])
            }

        return jsonify({
            "success": True,
            "data": result
        })

    except Exception as e:
        logger.error(f"NS-LE完整解析失败: {e}")
        return jsonify({"success": False, "error": "服务器内部错误", "code": ErrorCode.INTERNAL_ERROR.value}), 500


# ========== L3: 匹配结果存储 API ==========

@pipeline_ext_bp.route('/save/match-result', methods=['POST'])
def save_match_result():
    """
    L3: 保存匹配结果到 MySQL

    Body:
        {
            "student_id": "stu_001",
            "job_portrait_id": "portrait_python_backend_50",
            "total_score": 73.8,
            "d1_score": 80, "d2_score": 75, "d3_score": 70, "d4_score": 68,
            "d1_pass": true,
            "skill_coverage": 0.8,
            "is_matched": true,
            "match_level": "良好"
        }
    """
    data = request.get_json() or {}

    student_id = data.get('student_id', '')
    job_portrait_id = data.get('job_portrait_id', '')

    if not student_id or not job_portrait_id:
        return jsonify({"success": False, "error": "student_id 和 job_portrait_id 必填"}), 400

    try:
        from ..services.db import get_mysql_client
        mysql = get_mysql_client()

        import json
        result_json = {
            "d1_score": data.get('d1_score', 0),
            "d2_score": data.get('d2_score', 0),
            "d3_score": data.get('d3_score', 0),
            "d4_score": data.get('d4_score', 0),
            "total_score": data.get('total_score', 0),
            "skill_coverage": data.get('skill_coverage', 0),
            "d1_pass": data.get('d1_pass', False),
            "match_level": data.get('match_level', ''),
        }

        sql = """
        INSERT INTO match_result (
            student_id, job_portrait_id,
            d1_match, d2_match, d3_match, d4_match, total_score,
            d1_pass, skill_coverage, is_matched, match_level,
            result_json, created_at
        ) VALUES (
            :student_id, :job_portrait_id,
            :d1_score, :d2_score, :d3_score, :d4_score, :total_score,
            :d1_pass, :skill_coverage, :is_matched, :match_level,
            :result_json, NOW()
        )
        ON DUPLICATE KEY UPDATE
            total_score = VALUES(total_score),
            skill_coverage = VALUES(skill_coverage),
            is_matched = VALUES(is_matched),
            result_json = VALUES(result_json)
        """

        mysql.execute(sql, {
            "student_id": student_id,
            "job_portrait_id": job_portrait_id,
            "d1_score": data.get('d1_score', 0),
            "d2_score": data.get('d2_score', 0),
            "d3_score": data.get('d3_score', 0),
            "d4_score": data.get('d4_score', 0),
            "total_score": data.get('total_score', 0),
            "d1_pass": data.get('d1_pass', False),
            "skill_coverage": data.get('skill_coverage', 0),
            "is_matched": data.get('is_matched', False),
            "match_level": data.get('match_level', ''),
            "result_json": json.dumps(result_json)
        })

        return jsonify({
            "success": True,
            "data": {
                "student_id": student_id,
                "job_portrait_id": job_portrait_id,
                "saved": True
            }
        })

    except Exception as e:
        logger.error(f"保存匹配结果失败: {e}")
        return jsonify({"success": False, "error": "服务器内部错误", "code": ErrorCode.INTERNAL_ERROR.value}), 500


@pipeline_ext_bp.route('/save/skill-gap', methods=['POST'])
def save_skill_gap():
    """
    L3: 保存技能差距分析到 MySQL

    Body:
        {
            "student_id": "stu_001",
            "job_portrait_id": "portrait_python_backend_50",
            "critical_count": 3,
            "quick_learn_count": 5,
            "total_gap_count": 8,
            "gap_skills": [
                {"skill": "Kubernetes", "gap": 3, "priority": "critical"},
                {"skill": "Redis", "gap": 1, "priority": "quick_learn"}
            ]
        }
    """
    data = request.get_json() or {}

    student_id = data.get('student_id', '')
    job_portrait_id = data.get('job_portrait_id', '')

    if not student_id or not job_portrait_id:
        return jsonify({"success": False, "error": "student_id 和 job_portrait_id 必填"}), 400

    try:
        from ..services.db import get_mysql_client
        mysql = get_mysql_client()

        import json
        gap_json = {
            "critical_count": data.get('critical_count', 0),
            "quick_learn_count": data.get('quick_learn_count', 0),
            "gap_skills": data.get('gap_skills', [])
        }

        sql = """
        INSERT INTO skill_gap (
            student_id, job_portrait_id,
            critical_count, quick_learn_count, total_gap_count,
            gap_json, created_at
        ) VALUES (
            :student_id, :job_portrait_id,
            :critical_count, :quick_learn_count, :total_gap_count,
            :gap_json, NOW()
        )
        ON DUPLICATE KEY UPDATE
            critical_count = VALUES(critical_count),
            quick_learn_count = VALUES(quick_learn_count),
            total_gap_count = VALUES(total_gap_count),
            gap_json = VALUES(gap_json)
        """

        mysql.execute(sql, {
            "student_id": student_id,
            "job_portrait_id": job_portrait_id,
            "critical_count": data.get('critical_count', 0),
            "quick_learn_count": data.get('quick_learn_count', 0),
            "total_gap_count": data.get('total_gap_count', 0),
            "gap_json": json.dumps(gap_json)
        })

        return jsonify({
            "success": True,
            "data": {
                "student_id": student_id,
                "job_portrait_id": job_portrait_id,
                "saved": True
            }
        })

    except Exception as e:
        logger.error(f"保存技能差距失败: {e}")
        return jsonify({"success": False, "error": "服务器内部错误", "code": ErrorCode.INTERNAL_ERROR.value}), 500


@pipeline_ext_bp.route('/save/career-path', methods=['POST'])
def save_career_path():
    """
    L3: 保存职业路径推荐到 MySQL

    Body:
        {
            "student_id": "stu_001",
            "job_portrait_id": "portrait_python_backend_50",
            "recommended_dir": "高级后端工程师",
            "promotion_steps": 4,
            "lateral_count": 3,
            "path_json": {...}
        }
    """
    data = request.get_json() or {}

    student_id = data.get('student_id', '')
    job_portrait_id = data.get('job_portrait_id', '')

    if not student_id or not job_portrait_id:
        return jsonify({"success": False, "error": "student_id 和 job_portrait_id 必填"}), 400

    try:
        from ..services.db import get_mysql_client
        mysql = get_mysql_client()

        import json
        path_json = data.get('path_json', {})

        sql = """
        INSERT INTO career_path (
            student_id, job_portrait_id,
            recommended_dir, promotion_steps, lateral_count,
            path_json, created_at
        ) VALUES (
            :student_id, :job_portrait_id,
            :recommended_dir, :promotion_steps, :lateral_count,
            :path_json, NOW()
        )
        ON DUPLICATE KEY UPDATE
            recommended_dir = VALUES(recommended_dir),
            promotion_steps = VALUES(promotion_steps),
            lateral_count = VALUES(lateral_count),
            path_json = VALUES(path_json)
        """

        mysql.execute(sql, {
            "student_id": student_id,
            "job_portrait_id": job_portrait_id,
            "recommended_dir": data.get('recommended_dir', ''),
            "promotion_steps": data.get('promotion_steps', 0),
            "lateral_count": data.get('lateral_count', 0),
            "path_json": json.dumps(path_json)
        })

        return jsonify({
            "success": True,
            "data": {
                "student_id": student_id,
                "job_portrait_id": job_portrait_id,
                "saved": True
            }
        })

    except Exception as e:
        logger.error(f"保存职业路径失败: {e}")
        return jsonify({"success": False, "error": "服务器内部错误", "code": ErrorCode.INTERNAL_ERROR.value}), 500


@pipeline_ext_bp.route('/match/full', methods=['POST'])
def full_match_pipeline():
    """
    L3: 完整匹配管线 (匹配 + 差距分析 + 路径推荐 + 存储)

    Body:
        {
            "student_id": "stu_001",
            "job_portrait_id": "portrait_python_backend_50",
            "student_skills": [...],
            "job_skills": [...]
        }
    """
    data = request.get_json() or {}

    student_id = data.get('student_id', '')
    job_portrait_id = data.get('job_portrait_id', '')
    student_skills = _normalize_skills(data.get('student_skills', []))
    job_skills = _normalize_skills(data.get('job_skills', []))

    if not student_id or not job_portrait_id:
        return jsonify({"success": False, "error": "student_id 和 job_portrait_id 必填"}), 400

    try:
        from ..pipeline.entity_extractor.matcher import calculate_match_score, _get_match_level

        match_result = calculate_match_score(student_skills, job_skills)

        skill_coverage = match_result.get('skill_coverage', 0)
        is_matched = skill_coverage >= 0.8 and match_result.get('d1_pass', False)

        # match_level 走 matcher._get_match_level (统一权威源, 消除硬编码不一致 bug)
        match_level = _get_match_level(match_result['total_score'])

        missing_skills = match_result.get('missing_skills', [])
        level_gaps = match_result.get('gap_priorities', [])

        gap_skills = []
        critical_count = 0
        quick_learn_count = 0

        for gap in level_gaps:
            skill_name = gap.get('skill', '')
            gap_value = gap.get('gap', 0)
            priority = gap.get('priority', 'quick_learn')

            gap_skills.append({
                "skill": skill_name,
                "gap": gap_value,
                "priority": priority
            })

            if priority == "critical":
                critical_count += 1
            else:
                quick_learn_count += 1

        from ..pipeline.entity_extractor.graph_storage import GraphStorage
        storage = GraphStorage()
        career_info = storage.get_transfer_options(job_portrait_id.split('_')[1] if '_' in job_portrait_id else "工程师", min_jaccard=0.3)
        promotion_chains = storage.get_promotion_chains("中级工程师", min_chain_length=3)

        recommended_dir = promotion_chains[0]['positions'][-1] if promotion_chains else "高级工程师"
        promotion_steps = len(promotion_chains[0]['positions']) - 1 if promotion_chains else 3
        lateral_count = len(career_info)

        save_match_url = f"{request.host_url}api/pipeline/ext/save/match-result"
        save_gap_url = f"{request.host_url}api/pipeline/ext/save/skill-gap"
        save_path_url = f"{request.host_url}api/pipeline/ext/save/career-path"

        return jsonify({
            "success": True,
            "data": {
                "student_id": student_id,
                "job_portrait_id": job_portrait_id,
                "match_result": {
                    "total_score": match_result['total_score'],
                    "d1_score": match_result['d1_score'],
                    "d2_score": match_result['d2_score'],
                    "d3_score": match_result['d3_score'],
                    "d4_score": match_result['d4_score'],
                    "skill_coverage": skill_coverage,
                    "d1_pass": match_result['d1_score'] >= 60,
                    "is_matched": is_matched,
                    "match_level": match_level,
                    "matched_skills": match_result.get('matched_skills', []),
                    "missing_skills": missing_skills
                },
                "skill_gap": {
                    "critical_count": critical_count,
                    "quick_learn_count": quick_learn_count,
                    "total_gap_count": len(gap_skills),
                    "gap_skills": gap_skills
                },
                "career_path": {
                    "recommended_dir": recommended_dir,
                    "promotion_steps": promotion_steps,
                    "lateral_count": lateral_count,
                    "promotion_chains": promotion_chains[:3],
                    "transfer_options": career_info[:5]
                },
                "save_commands": {
                    "match_result": f"POST {save_match_url}",
                    "skill_gap": f"POST {save_gap_url}",
                    "career_path": f"POST {save_path_url}"
                }
            }
        })

    except Exception as e:
        logger.error(f"完整匹配管线失败: {e}")
        return jsonify({"success": False, "error": "服务器内部错误", "code": ErrorCode.INTERNAL_ERROR.value}), 500


# ========== Agent: 对话历史存储与统计 API ==========

@pipeline_ext_bp.route('/agent/session/save', methods=['POST'])
def save_agent_session():
    """
    Agent: 保存对话历史

    Body:
        {
            "session_id": "sess_001",
            "student_id": "stu_001",
            "title": "职业规划咨询",
            "messages": [
                {"role": "user", "content": "我想了解Python后端发展"},
                {"role": "assistant", "content": "Python后端是个好方向..."}
            ]
        }
    """
    data = request.get_json() or {}

    session_id = data.get('session_id', '')
    student_id = data.get('student_id', '')
    title = data.get('title', '')
    messages = data.get('messages', [])

    if not session_id or not messages:
        return jsonify({
            "success": False,
            "error": "session_id 和 messages 必填"
        }), 400

    try:
        from ..services.db import get_mysql_client
        mysql = get_mysql_client()

        import json
        import time

        conv_sql = """
        INSERT INTO conversation (session_id, student_id, title, status, message_count, updated_at)
        VALUES (:session_id, :student_id, :title, 'active', :message_count, NOW())
        ON DUPLICATE KEY UPDATE
            message_count = VALUES(message_count),
            updated_at = NOW()
        """
        mysql.execute(conv_sql, {
            "session_id": session_id,
            "student_id": student_id,
            "title": title or f"对话 {session_id}",
            "message_count": len(messages)
        })

        msg_sql = """
        INSERT INTO conversation_message (session_id, message_id, role, content, created_at)
        VALUES (:session_id, :message_id, :role, :content, FROM_UNIXTIME(:timestamp))
        """
        for i, msg in enumerate(messages):
            msg_id = f"{session_id}_{int(time.time() * 1000)}_{i}"
            mysql.execute(msg_sql, {
                "session_id": session_id,
                "message_id": msg_id,
                "role": msg.get('role', 'user'),
                "content": msg.get('content', ''),
                "timestamp": int(time.time())
            })

        return jsonify({
            "success": True,
            "data": {
                "session_id": session_id,
                "messages_saved": len(messages)
            }
        })

    except Exception as e:
        logger.error(f"保存对话历史失败: {e}")
        return jsonify({"success": False, "error": "服务器内部错误", "code": ErrorCode.INTERNAL_ERROR.value}), 500


@pipeline_ext_bp.route('/agent/stats', methods=['GET'])
def get_agent_stats():
    """
    Agent: 获取工具执行统计

    Query:
        ?session_id=xxx  (可选)
    """
    session_id = request.args.get('session_id', '')

    try:
        from ..services.db import get_mysql_client
        mysql = get_mysql_client()

        if session_id:
            sql = """
            SELECT role, COUNT(*) as count,
                   AVG(LENGTH(content)) as avg_content_length
            FROM conversation_message
            WHERE session_id = :session_id
            GROUP BY role
            """
            rows = mysql.fetch_all(sql, {"session_id": session_id})
        else:
            sql = """
            SELECT 'total_sessions' as metric, COUNT(DISTINCT session_id) as value
            FROM conversation
            UNION ALL
            SELECT 'total_messages' as metric, COUNT(*) as value
            FROM conversation_message
            UNION ALL
            SELECT 'avg_messages_per_session' as metric,
                   COUNT(*) / NULLIF(COUNT(DISTINCT session_id), 0) as value
            FROM conversation_message
            """
            rows = mysql.fetch_all(sql, {})

        return jsonify({
            "success": True,
            "data": {
                "session_id": session_id or "all",
                "stats": rows
            }
        })

    except Exception as e:
        logger.error(f"获取Agent统计失败: {e}")
        return jsonify({"success": False, "error": "服务器内部错误", "code": ErrorCode.INTERNAL_ERROR.value}), 500


@pipeline_ext_bp.route('/agent/session/<session_id>/messages', methods=['GET'])
def get_agent_session_messages(session_id: str):
    """
    Agent: 获取对话消息历史

    Path:
        session_id: 会话ID
    """
    limit = request.args.get('limit', 50, type=int)
    offset = request.args.get('offset', 0, type=int)

    try:
        from ..services.db import get_mysql_client
        mysql = get_mysql_client()

        sql = """
        SELECT message_id, role, content, created_at
        FROM conversation_message
        WHERE session_id = :session_id
        ORDER BY created_at ASC
        LIMIT :limit OFFSET :offset
        """
        rows = mysql.fetch_all(sql, {
            "session_id": session_id,
            "limit": limit,
            "offset": offset
        })

        return jsonify({
            "success": True,
            "data": {
                "session_id": session_id,
                "messages": rows,
                "count": len(rows)
            }
        })

    except Exception as e:
        logger.error(f"获取对话消息失败: {e}")
        return jsonify({"success": False, "error": "服务器内部错误", "code": ErrorCode.INTERNAL_ERROR.value}), 500


@pipeline_ext_bp.route('/match/list', methods=['GET'])
def list_match_results():
    """
    L3: 查询匹配结果列表

    Query:
        ?student_id=xxx&limit=10&offset=0
    """
    student_id = request.args.get('student_id', '')
    limit = request.args.get('limit', 10, type=int)
    offset = request.args.get('offset', 0, type=int)

    try:
        from ..services.db import get_mysql_client
        mysql = get_mysql_client()

        if student_id:
            sql = """
            SELECT id as match_id, student_id, job_portrait_id,
                   total_score,
                   d1_match as d1_score, d2_match as d2_score,
                   d3_match as d3_score, d4_match as d4_score,
                   is_matched, created_at
            FROM match_result
            WHERE student_id = :student_id
            ORDER BY total_score DESC
            LIMIT :limit OFFSET :offset
            """
            results = mysql.fetch_all(sql, {
                "student_id": student_id,
                "limit": limit,
                "offset": offset
            })
        else:
            sql = """
            SELECT id as match_id, student_id, job_portrait_id,
                   total_score,
                   d1_match as d1_score, d2_match as d2_score,
                   d3_match as d3_score, d4_match as d4_score,
                   is_matched, created_at
            FROM match_result
            ORDER BY total_score DESC
            LIMIT :limit OFFSET :offset
            """
            results = mysql.fetch_all(sql, {
                "limit": limit,
                "offset": offset
            })

        return jsonify({
            "success": True,
            "data": {
                "list": results,
                "count": len(results),
                "limit": limit,
                "offset": offset
            }
        })

    except Exception as e:
        logger.error(f"查询匹配结果列表失败: {e}")
        return jsonify({"success": False, "error": "服务器内部错误", "code": ErrorCode.INTERNAL_ERROR.value}), 500


@pipeline_ext_bp.route('/match/trend', methods=['GET'])
def get_match_trend():
    """
    分析学生匹配历史趋势

    Query:
        ?student_id=stu_001
    """
    student_id = request.args.get('student_id', '')

    if not student_id:
        return jsonify({"success": False, "error": "student_id 参数不能为空"}), 400

    try:
        from ..services.db import get_mysql_client
        mysql = get_mysql_client()

        sql = """
        SELECT mr.total_score,
               mr.d1_match as d1_score, mr.d2_match as d2_score,
               mr.d3_match as d3_score, mr.d4_match as d4_score,
               mr.skill_coverage, mr.is_matched, mr.match_level,
               mr.created_at,
               jp.position_name
        FROM match_result mr
        LEFT JOIN job_portrait_4d jp ON mr.job_portrait_id = jp.portrait_id
        WHERE mr.student_id = :student_id
        ORDER BY mr.created_at ASC
        LIMIT 50
        """
        records = mysql.fetch_all(sql, {"student_id": student_id})

        if not records:
            return jsonify({
                "success": True,
                "data": {
                    "student_id": student_id,
                    "record_count": 0,
                    "trend": "暂无匹配记录",
                    "suggestions": ["建议先进行人岗匹配测试"]
                }
            })

        total_scores = [r['total_score'] for r in records if r['total_score']]
        d1_scores = [r['d1_score'] for r in records if r['d1_score']]
        d2_scores = [r['d2_score'] for r in records if r['d2_score']]
        d3_scores = [r['d3_score'] for r in records if r['d3_score']]
        d4_scores = [r['d4_score'] for r in records if r['d4_score']]

        avg_total = round(sum(total_scores) / len(total_scores), 1) if total_scores else 0
        avg_d1 = round(sum(d1_scores) / len(d1_scores), 1) if d1_scores else 0
        avg_d2 = round(sum(d2_scores) / len(d2_scores), 1) if d2_scores else 0
        avg_d3 = round(sum(d3_scores) / len(d3_scores), 1) if d3_scores else 0
        avg_d4 = round(sum(d4_scores) / len(d4_scores), 1) if d4_scores else 0

        first_score = total_scores[0] if total_scores else 0
        last_score = total_scores[-1] if total_scores else 0
        score_change = round(last_score - first_score, 1) if total_scores else 0

        matched_count = sum(1 for r in records if r['is_matched'])
        match_rate = round(matched_count / len(records) * 100, 1) if records else 0

        suggestions = []
        if score_change > 5:
            suggestions.append("匹配分数持续上升，表现越来越出色")
        elif score_change < -5:
            suggestions.append("匹配分数有所下降，建议回顾近期简历更新")
        else:
            suggestions.append("匹配分数整体稳定")

        if avg_d1 < 60:
            suggestions.append("D1基础要求偏低，建议提升学历或专业匹配度")
        if avg_d2 < 70:
            suggestions.append("D2职业技能有待提升，建议加强技能学习")
        if avg_d3 < 65:
            suggestions.append("D3职业素养需要加强，注重软技能培养")
        if avg_d4 < 60:
            suggestions.append("D4发展潜力有提升空间，建议参与更多项目竞赛")
        if match_rate < 50:
            suggestions.append(f"历史匹配率仅{match_rate}%，建议拓宽岗位选择范围")
        else:
            suggestions.append(f"历史匹配率{match_rate}%，整体表现良好")

        return jsonify({
            "success": True,
            "data": {
                "student_id": student_id,
                "record_count": len(records),
                "avg_scores": {
                    "total": avg_total,
                    "d1": avg_d1,
                    "d2": avg_d2,
                    "d3": avg_d3,
                    "d4": avg_d4
                },
                "trend": {
                    "first_score": first_score,
                    "last_score": last_score,
                    "score_change": score_change,
                    "direction": "上升" if score_change > 2 else ("下降" if score_change < -2 else "平稳")
                },
                "match_rate": match_rate,
                "matched_count": matched_count,
                "history": [
                    {
                        "total_score": r['total_score'],
                        "position_name": r['position_name'] or '未知岗位',
                        "match_level": r['match_level'] or '',
                        "created_at": str(r['created_at'])[:19] if r['created_at'] else ''
                    }
                    for r in records[-10:]
                ],
                "suggestions": suggestions
            }
        })

    except Exception as e:
        logger.error(f"获取匹配趋势失败: {e}")
        return jsonify({"success": False, "error": "服务器内部错误", "code": ErrorCode.INTERNAL_ERROR.value}), 500


@pipeline_ext_bp.route('/db/health', methods=['GET'])
def db_health_check():
    """
    数据库健康检查

    检查 MySQL, Neo4j, Milvus, Redis 四个数据库的连接状态和响应时间
    """
    import time

    results = {
        "mysql": {"status": "unknown", "response_time_ms": None, "error": None},
        "neo4j": {"status": "unknown", "response_time_ms": None, "error": None},
        "milvus": {"status": "unknown", "response_time_ms": None, "error": None},
        "redis": {"status": "unknown", "response_time_ms": None, "error": None}
    }

    def _retry_call(func, *args, retries=2, **kwargs):
        last_error = None
        for attempt in range(retries):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                last_error = e
                if attempt < retries - 1:
                    time.sleep(0.1)
        raise last_error

    try:
        from ..services.db import get_mysql_client
        start = time.time()
        mysql = get_mysql_client()
        _retry_call(mysql.execute_single, "SELECT 1 AS v")
        results["mysql"]["status"] = "healthy"
        results["mysql"]["response_time_ms"] = round((time.time() - start) * 1000, 2)
    except Exception as e:
        results["mysql"]["status"] = "unhealthy"
        results["mysql"]["error"] = str(e)

    try:
        from ..services.db import get_neo4j_client
        start = time.time()
        neo4j = get_neo4j_client()
        _retry_call(neo4j.execute_single, "RETURN 1 AS v")
        results["neo4j"]["status"] = "healthy"
        results["neo4j"]["response_time_ms"] = round((time.time() - start) * 1000, 2)
    except Exception as e:
        results["neo4j"]["status"] = "unhealthy"
        results["neo4j"]["error"] = str(e)

    try:
        from ..services.db import get_milvus_client
        start = time.time()
        milvus = get_milvus_client()
        _retry_call(milvus.connect)
        results["milvus"]["status"] = "healthy"
        results["milvus"]["response_time_ms"] = round((time.time() - start) * 1000, 2)
    except Exception as e:
        results["milvus"]["status"] = "unhealthy"
        results["milvus"]["error"] = str(e)

    try:
        from ..services.db import get_redis_client
        start = time.time()
        redis_client = get_redis_client()
        _retry_call(redis_client.test_connection)
        results["redis"]["status"] = "healthy"
        results["redis"]["response_time_ms"] = round((time.time() - start) * 1000, 2)
    except Exception as e:
        results["redis"]["status"] = "unhealthy"
        results["redis"]["error"] = str(e)

    all_healthy = all(r["status"] == "healthy" for r in results.values())

    return jsonify({
        "success": True,
        "data": {
            "overall": "healthy" if all_healthy else "degraded",
            "databases": results
        }
    })
