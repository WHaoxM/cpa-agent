"""
Tool 4: pipeline_run — L0-L4 管线触发
智能体自主决定何时触发数据处理管线 (简历解析/画像生成/匹配/报告)
"""

import json
import uuid
from datetime import datetime
from typing import Dict, Optional

from .base import BaseTool
from ...utils.logger import get_logger
from ...services.db.mysql_client import MySQLClient, get_mysql_client

logger = get_logger('cpa-agent.tool.pipeline_run')

PIPELINE_TASKS = {
    "L0_parse_resume": "简历解析管线",
    "L1_build_graph": "知识图谱构建管线",
    "L2_generate_portrait": "画像生成管线",
    "L3_run_match": "人岗匹配管线",
    "L4_generate_report": "职业报告生成管线",
}


class PipelineRunTool(BaseTool):
    """L0-L4 管线触发工具"""

    def __init__(self):
        super().__init__()
        self._mysql = None

    @property
    def mysql(self) -> MySQLClient:
        if self._mysql is None:
            self._mysql = get_mysql_client()
        return self._mysql

    @property
    def name(self) -> str:
        return "pipeline_run"

    @property
    def description(self) -> str:
        return """\
【管线触发 — L0-L4 Pipeline】
触发 CPA-Agent 的数据处理管线, 执行简历解析、画像生成、匹配分析、报告生成等任务。

【可触发的管线】
- L0_parse_resume: 简历解析 (MinerU + YOLOv10 + Qwen3-SFT)
- L1_build_graph: 知识图谱构建 (技能骨架 + 岗位聚合 + 路径图谱)
- L2_generate_portrait: 画像生成 (学生四维画像 + 岗位四维画像)
- L3_run_match: 人岗匹配 (D1硬约束 + D2技能覆盖 + D3/D4加权)
- L4_generate_report: 报告生成 (6章结构, 模板70% + LLM30%)

【使用场景】
- "帮我分析简历" → L0_parse_resume
- "重新生成我的画像" → L2_generate_portrait
- "帮我匹配Java后端" → L3_run_match
- "生成职业报告" → L4_generate_report

【返回内容】
- 任务ID (task_id)
- 执行状态 (pending/running/completed/failed)
- 简要结果摘要"""

    @property
    def parameters(self) -> Dict[str, str]:
        return {
            "pipeline": "管线名称 (L0_parse_resume / L1_build_graph / L2_generate_portrait / L3_run_match / L4_generate_report)",
            "params": "管线参数 (JSON 字符串, 如 student_id / job_id 等)"
        }

    def _create_task(self, pipeline: str, params: dict) -> str:
        task_id = f"task_{uuid.uuid4().hex[:12]}"

        sql = """
        INSERT INTO pipeline_task
        (task_id, pipeline, status, params_json, student_id, job_id, triggered_by, created_at)
        VALUES (:task_id, :pipeline, :status, :params_json, :student_id, :job_id, :triggered_by, :created_at)
        """

        student_id = params.get("student_id")
        job_id = params.get("job_id")

        self.mysql.execute(
            sql,
            {
                "task_id": task_id,
                "pipeline": pipeline,
                "status": "pending",
                "params_json": json.dumps(params),
                "student_id": student_id,
                "job_id": job_id,
                "triggered_by": "agent",
                "created_at": datetime.now(),
            },
            commit=True
        )

        logger.info(f"创建管线任务: task_id={task_id}, pipeline={pipeline}")
        return task_id

    def _run_pipeline_sync(self, pipeline: str, params: dict) -> str:
        task_id = self._create_task(pipeline, params)

        self.mysql.execute(
            "UPDATE pipeline_task SET status=:status, started_at=:started_at WHERE task_id=:task_id",
            {"status": "running", "started_at": datetime.now(), "task_id": task_id},
            commit=True
        )

        try:
            result = self._execute_pipeline(pipeline, params)

            self.mysql.execute(
                "UPDATE pipeline_task SET status=:status, completed_at=:completed_at, result_json=:result_json WHERE task_id=:task_id",
                {"status": "completed", "completed_at": datetime.now(), "result_json": json.dumps(result), "task_id": task_id},
                commit=True
            )

            return f"[pipeline_run] 任务完成\n任务ID: {task_id}\n管线: {pipeline}\n结果: {result.get('summary', '处理完成')}"

        except Exception as e:
            logger.error(f"管线执行失败: {e}")
            self.mysql.execute(
                "UPDATE pipeline_task SET status=:status, error_message=:error_message WHERE task_id=:task_id",
                {"status": "failed", "error_message": str(e), "task_id": task_id},
                commit=True
            )
            return f"[pipeline_run] 任务失败，请稍后重试"

    def _execute_pipeline(self, pipeline: str, params: dict) -> dict:
        if pipeline == "L0_parse_resume":
            return self._run_l0_parse_resume(params)
        elif pipeline == "L1_build_graph":
            return self._run_l1_build_graph(params)
        elif pipeline == "L2_generate_portrait":
            return self._run_l2_generate_portrait(params)
        elif pipeline == "L3_run_match":
            return self._run_l3_run_match(params)
        elif pipeline == "L4_generate_report":
            return self._run_l4_generate_report(params)
        return {"summary": "未知管线"}

    def _get_student_profile(self, student_id: str) -> Optional[Dict]:
        """从 MySQL 获取学生 profile 数据"""
        try:
            rows = self.mysql.fetch_all(
                """
                SELECT name, school, major, degree, graduation_year, profile_json
                FROM student_profile WHERE student_id = :student_id LIMIT 1
                """,
                {"student_id": student_id}
            )
            if rows:
                row = rows[0]
                profile = {
                    "name": row.get("name"),
                    "school": row.get("school"),
                    "major": row.get("major"),
                    "degree": row.get("degree"),
                    "graduation_year": row.get("graduation_year")
                }
                profile_json = row.get("profile_json")
                if profile_json:
                    if isinstance(profile_json, str):
                        import json
                        profile_json = json.loads(profile_json)
                    profile["profile_json"] = profile_json
                return profile
        except Exception as e:
            logger.warning(f"获取学生 profile 失败: {e}")
        return None

    def _run_l0_parse_resume(self, params: dict) -> dict:
        from ...pipeline.entity_extractor.l0_parser import L0ResumeParser
        student_id = params.get("student_id")
        file_path = params.get("file_path")
        text = params.get("text")

        try:
            parser = L0ResumeParser()
            if file_path:
                parse_result = parser.parse_file(file_path)
            elif text:
                parse_result = parser.parse_text(text)
            else:
                return {"success": False, "summary": "缺少 file_path 或 text 参数"}

            if not parse_result.success:
                return {"success": False, "summary": parse_result.error or "解析失败"}

            return {
                "success": True,
                "summary": f"简历解析完成，共 {len(parse_result.blocks)} 个文本块",
                "student_id": student_id,
                "block_count": len(parse_result.blocks)
            }
        except Exception as e:
            logger.error(f"L0 简历解析失败: {e}")
            return {"success": False, "summary": "解析失败，请稍后重试"}

    def _run_l1_build_graph(self, params: dict) -> dict:
        from ...pipeline.entity_extractor.nsle_pipeline import NSLEPipeline
        from ...pipeline.entity_extractor.graph_storage import GraphStorage
        from ...pipeline.entity_extractor.skill_extractor import extract_skills as dict_extract

        student_id = params.get("student_id")
        text = params.get("text")
        blocks = params.get("blocks", [])

        try:
            if text:
                pipeline = NSLEPipeline(use_deepseek=False)
                parse_result = pipeline.run_resume_text(text)
                skills_data = parse_result.get("skills", [])
            elif blocks:
                all_text = "\n".join([b.get("text", "") if isinstance(b, dict) else str(b) for b in blocks])
                skills_data = dict_extract(all_text)
            else:
                return {"success": False, "summary": "L1 构建缺少 text 或 blocks 参数"}

            skill_list = [s["name"] for s in skills_data] if isinstance(skills_data, list) else []
            if not skill_list:
                return {"success": False, "summary": "未提取到任何技能"}

            storage = GraphStorage()
            saved_count = storage.save_skills_to_graph(
                student_id=student_id or "unknown",
                skills=[{"name": s} for s in skill_list],
                source="resume"
            )

            return {
                "success": True,
                "summary": f"知识图谱构建完成，保存 {saved_count} 个技能节点",
                "student_id": student_id,
                "skill_count": saved_count
            }
        except Exception as e:
            logger.error(f"L1 图谱构建失败: {e}")
            return {"success": False, "summary": "图谱构建失败，请稍后重试"}

    def _run_l2_generate_portrait(self, params: dict) -> dict:
        from ...pipeline.entity_extractor.portrait_generator import PortraitGenerator
        from ...pipeline.entity_extractor.graph_storage import GraphStorage

        student_id = params.get("student_id")
        job_id = params.get("job_id")
        skills = params.get("skills", [])
        student_profile = params.get("student_profile")

        try:
            if not skills:
                storage = GraphStorage()
                graph_skills = storage.get_entity_skills(student_id or "unknown", entity_type="student")
                skills = [{"name": s["name"], "category": s.get("category", ""), "confidence": s.get("confidence", 0.5)} for s in graph_skills]

            if not skills:
                return {"success": False, "summary": "未找到学生技能数据，请先执行 L1 构建"}

            if not student_profile and student_id:
                student_profile = self._get_student_profile(student_id)

            portrait_gen = PortraitGenerator()
            portrait = portrait_gen.generate_student_portrait(
                student_id=student_id or "unknown",
                skills=skills,
                match_job_id=job_id,
                student_profile=student_profile
            )

            return {
                "success": True,
                "summary": f"画像生成完成，综合得分 {portrait.get('competitiveness', 0)}",
                "student_id": student_id,
                "d1": portrait.get("d1_score"),
                "d2": portrait.get("d2_score"),
                "d3": portrait.get("d3_score"),
                "d4": portrait.get("d4_score"),
                "competitiveness": portrait.get("competitiveness")
            }
        except Exception as e:
            logger.error(f"L2 画像生成失败: {e}")
            return {"success": False, "summary": "画像生成失败，请稍后重试"}

    def _run_l3_run_match(self, params: dict) -> dict:
        from ...pipeline.entity_extractor.matcher import SkillMatcher
        from ...pipeline.entity_extractor.skill_matcher import SkillMatcher as SemanticMatcher
        from ...pipeline.entity_extractor.graph_storage import GraphStorage

        student_id = params.get("student_id")
        job_portrait_id = params.get("job_portrait_id") or params.get("job_id")
        student_skills = params.get("student_skills", [])
        job_skills = params.get("job_skills", [])

        try:
            storage = GraphStorage()
            if not student_skills:
                graph_skills = storage.get_entity_skills(student_id or "unknown", entity_type="student")
                student_skills = [{"name": s["name"], "level": s.get("level", "了解"), "confidence": s.get("confidence", 0.5)} for s in graph_skills]

            if not job_portrait_id:
                return {"success": False, "summary": "缺少 job_portrait_id 参数"}

            job_cypher = "MATCH (j:JobPortrait {portrait_id: $portrait_id}) MATCH (j)-[r:REQUIRES|PREFERS|BONUS]->(s:Skill) RETURN s.name as name, r.level as level"
            from ...services.db.neo4j_client import Neo4jClient
            neo4j = Neo4jClient()
            job_result = neo4j.execute_query(job_cypher, {"portrait_id": job_portrait_id})
            if not job_result:
                return {"success": False, "summary": f"未找到岗位画像 {job_portrait_id}"}
            job_skills = [{"name": r["name"], "level": r.get("level", "必须")} for r in job_result]

            if not student_skills or not job_skills:
                return {"success": False, "summary": "学生或岗位技能列表为空"}

            matcher = SkillMatcher()
            semantic_matcher = SemanticMatcher()
            student_text = " ".join([s.get("name", "") for s in student_skills])
            _, semantic_matches = semantic_matcher.match_with_semantic(student_text)
            semantic_matches = semantic_matches or []

            result = matcher.calculate_match(student_skills, job_skills, semantic_matches=semantic_matches)

            return {
                "success": True,
                "summary": f"匹配完成，总分 {result.total_score}，匹配等级 {result.match_level}",
                "student_id": student_id,
                "job_portrait_id": job_portrait_id,
                "total_score": result.total_score,
                "d1_score": result.d1_score,
                "d2_score": result.d2_score,
                "d3_score": result.d3_score,
                "d4_score": result.d4_score,
                "skill_coverage": result.skill_coverage,
                "matched_count": len(result.matched_skills),
                "missing_count": len(result.missing_skills)
            }
        except Exception as e:
            logger.error(f"L3 匹配失败: {e}")
            return {"success": False, "summary": "匹配执行失败，请稍后重试"}

    def _run_l4_generate_report(self, params: dict) -> dict:
        from ...pipeline.entity_extractor.l4_report_generator import L4ReportGenerator

        student_id = params.get("student_id")
        job_portrait_id = params.get("job_portrait_id") or params.get("job_id")
        report_id = f"report_{uuid.uuid4().hex[:12]}"

        try:
            generator = L4ReportGenerator()
            report_result = generator.generate(
                student_id=student_id or "unknown",
                job_portrait_id=job_portrait_id or "unknown",
                report_id=report_id
            )

            if report_result.get("status") == "failed":
                return {"success": False, "summary": report_result.get("error", "报告生成失败")}

            return {
                "success": True,
                "summary": f"报告生成完成",
                "report_id": report_id,
                "student_id": student_id,
                "job_portrait_id": job_portrait_id
            }
        except Exception as e:
            logger.error(f"L4 报告生成失败: {e}")
            return {"success": False, "summary": "报告生成失败，请稍后重试"}

    def execute(self, **kwargs) -> str:
        pipeline = kwargs.get("pipeline", "")
        params_str = kwargs.get("params", "{}")

        logger.info(f"pipeline_run 执行: pipeline={pipeline}, params={params_str}")

        valid_pipelines = set(PIPELINE_TASKS.keys())
        if pipeline not in valid_pipelines:
            return f"[pipeline_run] 错误: 未知管线 '{pipeline}', 可选: {', '.join(sorted(valid_pipelines))}"

        try:
            params = json.loads(params_str) if isinstance(params_str, str) else params_str
        except json.JSONDecodeError:
            return f"[pipeline_run] 错误: params 不是有效的 JSON 格式"

        return self._run_pipeline_sync(pipeline, params)
