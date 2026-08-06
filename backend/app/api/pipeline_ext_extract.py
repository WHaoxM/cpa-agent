"""
Pipeline_ext Extract 子模块

L0+L1 文本解析与技能抽取 API (20 路由):
- POST /extract             技能抽取 + 图谱存储
- POST /extract/skills      仅抽取技能(不存图谱)
- POST /report/full         生成完整报告
- POST /report/html         导出 HTML 报告
- POST /report/summary      仅生成 LLM 摘要
- POST /match               人岗匹配
- POST /match/auto          自动人岗匹配(从文本抽取后匹配)
- GET  /recommend           技能推荐
- GET  /path                学习路径查找
- GET  /graph               获取技能图谱
- POST /portrait/student    生成学生画像
- POST /portrait/job        生成岗位画像
- POST /report/match        生成匹配报告
- POST /report/career       生成职业发展报告
- POST /parse/resume        L0 文本解析(简历)
- POST /parse/jd            L0 文本解析(JD)
- POST /parse/full          L0 完整解析(简历+JD)
- POST /nsle/resume         NS-LE 简历解析
- POST /nsle/jd             NS-LE JD 解析
- POST /nsle/full           NS-LE 完整解析

依赖: ..pipeline.entity_extractor (EntityPipeline, matcher, graph_storage, l4_report_generator 等)
"""

import uuid
from flask import request, jsonify

from ._helpers import clamp_pagination, safe_error
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
        return jsonify(safe_error("技能抽取", e, message="服务器内部错误")), 500


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
        return jsonify(safe_error("技能抽取", e, message="服务器内部错误")), 500


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
        return jsonify(safe_error("生成完整报告", e, message="服务器内部错误")), 500


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
        return jsonify(safe_error("生成HTML报告", e, message="服务器内部错误")), 500


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
        return jsonify(safe_error("生成摘要", e, message="服务器内部错误")), 500


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
        return jsonify(safe_error("人岗匹配", e, message="服务器内部错误")), 500


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
        return jsonify(safe_error("自动匹配", e, message="服务器内部错误")), 500


@pipeline_ext_bp.route('/recommend', methods=['GET'])
def recommend_skills():
    """
    技能推荐

    Query:
        ?skills=Python,Django,MySQL&limit=5
    """
    skills_str = request.args.get('skills', '')
    limit, _ = clamp_pagination(request.args.get('limit', type=int), None, default_limit=5)

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
        return jsonify(safe_error("技能推荐", e, message="服务器内部错误")), 500


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
        return jsonify(safe_error("路径查找", e, message="服务器内部错误")), 500


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
        return jsonify(safe_error("获取技能图谱", e, message="服务器内部错误")), 500


@pipeline_ext_bp.route('/portrait/student', methods=['POST'])
def create_student_portrait():
    """
    L2: 生成学生画像

    Body:
        {
            "student_id": "stu_001",
            "skills": [{"name": "Python", "category": "编程语言", "confidence": 0.85}],
            "target_job_id": "可选",
            "student_profile": {
                "name": "可选", "school": "可选", "major": "可选",
                "degree": "可选", "gpa": 3.5,
                "certificates": [], "projects": []
            },
            "target_job_skills": [{"name": "Python", "level": "必须"}]
        }
    """
    data = request.get_json() or {}
    student_id = data.get('student_id', f'stu_{uuid.uuid4().hex[:8]}')
    skills = _normalize_skills(data.get('skills', []))
    student_profile = data.get('student_profile')
    if student_profile is not None and not isinstance(student_profile, dict):
        student_profile = None
    target_job_skills = data.get('target_job_skills')
    if target_job_skills is not None:
        target_job_skills = _normalize_skills(target_job_skills)

    if not skills:
        return jsonify({"success": False, "error": "skills 不能为空"}), 400

    try:
        from ..pipeline.entity_extractor.portrait_generator import generate_student_portrait
        portrait = generate_student_portrait(
            student_id,
            skills,
            student_profile=student_profile,
            target_job_skills=target_job_skills,
        )

        return jsonify({
            "success": True,
            "data": portrait
        })

    except Exception as e:
        return jsonify(safe_error("生成学生画像", e, message="服务器内部错误")), 500


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
        return jsonify(safe_error("生成岗位画像", e, message="服务器内部错误")), 500


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
        return jsonify(safe_error("生成匹配报告", e, message="服务器内部错误")), 500


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
        return jsonify(safe_error("生成职业报告", e, message="服务器内部错误")), 500


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
        return jsonify(safe_error("简历解析", e, message="服务器内部错误")), 500


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
        return jsonify(safe_error("JD解析", e, message="服务器内部错误")), 500


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
        return jsonify(safe_error("完整解析", e, message="服务器内部错误")), 500


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
                "entities": result.get("entities", {}),
                # Resume Structure Truth: LLM canonical profile + field-presence gate
                "student_profile": result.get("student_profile") or {},
                "structure_validation": result.get("structure_validation") or {
                    "ok": False,
                    "issues": ["missing_structure_validation"],
                },
            }
        })

    except Exception as e:
        return jsonify(safe_error("NS-LE简历解析", e, message="服务器内部错误")), 500


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
        return jsonify(safe_error("NS-LE JD解析", e, message="服务器内部错误")), 500


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
        return jsonify(safe_error("NS-LE完整解析", e, message="服务器内部错误")), 500
