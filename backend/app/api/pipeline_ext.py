"""
Pipeline API - 实体抽取管线 API
封装 L0/L1 技能抽取、人岗匹配、技能推荐
"""

import uuid
from flask import Blueprint, request, jsonify

from ..pipeline.entity_extractor.entity_pipeline import EntityPipeline
from ..pipeline.entity_extractor.graph_storage import GraphStorage
from ..pipeline.entity_extractor.matcher import calculate_match_score
from ..pipeline.entity_extractor.portrait_generator import generate_student_portrait, generate_job_portrait
from ..pipeline.entity_extractor.report_generator import generate_match_report, generate_career_report
from ..utils.logger import get_logger

logger = get_logger('cpa-agent.api.pipeline_ext')

pipeline_ext_bp = Blueprint('pipeline_ext', __name__, url_prefix='/api/pipeline/ext')


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
            "error": str(e)
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
        return jsonify({"success": False, "error": str(e)}), 500


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
    student_skills = data.get('student_skills', [])
    job_skills = data.get('job_skills', [])

    if not student_skills:
        return jsonify({"success": False, "error": "student_skills 不能为空"}), 400
    if not job_skills:
        return jsonify({"success": False, "error": "job_skills 不能为空"}), 400

    try:
        result = calculate_match_score(student_skills, job_skills)

        return jsonify({
            "success": True,
            "data": result
        })

    except Exception as e:
        logger.error(f"人岗匹配失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


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

        student_skills_input = [
            {"name": s["name"], "level": "掌握" if s["confidence"] > 0.6 else "熟悉", "confidence": s["confidence"]}
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
        return jsonify({"success": False, "error": str(e)}), 500


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
        return jsonify({"success": False, "error": str(e)}), 500


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
        storage = GraphStorage()
        path = storage.find_learning_path(from_skill, to_skill, max_hops=max_hops)

        return jsonify({
            "success": True,
            "data": path
        })

    except Exception as e:
        logger.error(f"路径查找失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


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
        storage = GraphStorage()
        graph = storage.get_skill_graph(skills)

        return jsonify({
            "success": True,
            "data": graph
        })

    except Exception as e:
        logger.error(f"获取技能图谱失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


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
    skills = data.get('skills', [])
    target_job_id = data.get('target_job_id')

    if not skills:
        return jsonify({"success": False, "error": "skills 不能为空"}), 400

    try:
        portrait = generate_student_portrait(student_id, skills)

        return jsonify({
            "success": True,
            "data": portrait
        })

    except Exception as e:
        logger.error(f"生成学生画像失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


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
    skills = data.get('skills', [])
    salary_range = data.get('salary_range')

    try:
        portrait = generate_job_portrait(job_id, position_name, skills)

        return jsonify({
            "success": True,
            "data": portrait
        })

    except Exception as e:
        logger.error(f"生成岗位画像失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500


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
    student_skills = data.get('student_skills', [])
    job_id = data.get('job_id', f'job_{uuid.uuid4().hex[:8]}')
    job_name = data.get('job_name', '未知岗位')
    job_skills = data.get('job_skills', [])

    if not student_skills:
        return jsonify({"success": False, "error": "student_skills 不能为空"}), 400
    if not job_skills:
        return jsonify({"success": False, "error": "job_skills 不能为空"}), 400

    try:
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
        return jsonify({"success": False, "error": str(e)}), 500


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
    skills = data.get('skills', [])

    if not skills:
        return jsonify({"success": False, "error": "skills 不能为空"}), 400

    try:
        report = generate_career_report(student_id, skills)

        return jsonify({
            "success": True,
            "data": report
        })

    except Exception as e:
        logger.error(f"生成职业报告失败: {e}")
        return jsonify({"success": False, "error": str(e)}), 500
