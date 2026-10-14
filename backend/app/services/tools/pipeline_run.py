"""
Tool 4: pipeline_run — L0-L4 管线触发
智能体自主决定何时触发数据处理管线 (简历解析/画像生成/匹配/报告)
"""

import json
import os
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
- L2_generate_portrait: 画像生成 (学生四维画像 + 岗位四维画像)
- L3_run_match: 人岗匹配 (D1硬约束 + D2技能覆盖 + D3/D4加权)
- L4_generate_report: 报告生成 (6章结构, 模板70% + LLM30%)

L1 管理性图谱构建不对 Agent 开放；个人图谱由已确认资料发布流程维护。

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
            "pipeline": "管线名称 (L0_parse_resume / L2_generate_portrait / L3_run_match / L4_generate_report)",
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

        try:
            # The delegated BasePipeline owns the sole pending→running claim
            # and authoritative terminal write. A wrapper pre-write would make
            # that CAS fail; a wrapper post-write could roll back a confirmation.
            result = self._execute_pipeline(pipeline, params, task_id=task_id)
            if not isinstance(result, dict) or result.get("success") is False or result.get("status") == "failed":
                raise RuntimeError(str((result or {}).get("summary") or "管线未完成"))

            final_status = result.get("status", "completed")
            if final_status not in {"completed", "needs_input", "partial"}:
                raise RuntimeError("管线返回未知完成状态")
            if isinstance(self.mysql, MySQLClient):
                rows = self.mysql.fetch_all(
                    "SELECT status FROM pipeline_task WHERE task_id=:task_id "
                    "AND student_id=:student_id LIMIT 1",
                    {"task_id": task_id, "student_id": params["student_id"]},
                )
                if not rows or rows[0].get("status") != final_status:
                    raise RuntimeError("管线结果未通过权威任务状态回读")

            summary = result.get("summary", "处理完成")
            confirmation_workflow = None
            if pipeline == "L0_parse_resume" and final_status == "needs_input":
                try:
                    from ..agent_runtime.resume_confirmation import run_confirmation
                    confirmation_workflow = run_confirmation(
                        student_id=params["student_id"], task_id=task_id)
                except Exception:
                    logger.exception("Durable confirmation unavailable for %s", task_id)
                    confirmation_workflow = {"workflow": "resume_confirmation",
                        "resume_state": "not_available", "next_action": "confirm_resume"}
            return json.dumps({
                "confirmation_workflow": confirmation_workflow,
                "message": f"[pipeline_run] {summary}\n任务ID: {task_id}\n管线: {pipeline}",
                "summary": summary,
                "task_id": task_id,
                "pipeline": pipeline,
                "status": final_status,
                "result": result,
                "artifact_candidates": self._artifact_candidates_for_pipeline(
                    task_id=task_id,
                    pipeline=pipeline,
                    status=final_status,
                    result=result,
                ),
            }, ensure_ascii=False)

        except Exception as e:
            logger.error(f"管线执行失败: {e}")
            observed_status = "failed"
            if isinstance(self.mysql, MySQLClient):
                try:
                    from sqlalchemy import text

                    with self.mysql.engine.begin() as conn:
                        # The delegated pipeline owns running→failed. Only a
                        # setup error before its claim belongs to this wrapper;
                        # never kill a currently running or terminal task.
                        conn.execute(text(
                            "UPDATE pipeline_task SET status=:status, error_message=:error_message "
                            "WHERE task_id=:task_id AND student_id=:student_id "
                            "AND status='pending'"
                        ), {"status": "failed", "error_message": str(e),
                            "task_id": task_id, "student_id": params["student_id"]})
                        row = conn.execute(text(
                            "SELECT status FROM pipeline_task WHERE task_id=:task_id "
                            "AND student_id=:student_id LIMIT 1"
                        ), {"task_id": task_id, "student_id": params["student_id"]}).mappings().first()
                        observed_status = row["status"] if row else "unverified"
                except Exception:
                    logger.exception("管线异常后无法回读权威任务状态: %s", task_id)
                    observed_status = "unverified"
            else:
                # Memory fakes used by unit tests do not expose transaction
                # rowcount or a shared task-state store.
                self.mysql.execute(
                    "UPDATE pipeline_task SET status=:status, error_message=:error_message "
                    "WHERE task_id=:task_id AND status IN ('pending','running')",
                    {"status": "failed", "error_message": str(e), "task_id": task_id},
                    commit=True,
                )
            if observed_status != "failed":
                return json.dumps({
                    "success": False,
                    "message": "管线工具响应未完成，请按任务 ID 回读权威状态",
                    "summary": "管线结果需回读",
                    "task_id": task_id,
                    "pipeline": pipeline,
                    "status": "unverified",
                    "authoritative_task_status": observed_status,
                    "artifact_candidates": [],
                }, ensure_ascii=False)
            return json.dumps({
                "message": "[pipeline_run] 任务失败，请稍后重试",
                "summary": f"{PIPELINE_TASKS.get(pipeline, pipeline)}失败",
                "task_id": task_id,
                "pipeline": pipeline,
                "status": "failed",
                "artifact_candidates": self._artifact_candidates_for_pipeline(
                    task_id=task_id,
                    pipeline=pipeline,
                    status="failed",
                    result={"summary": "任务失败"},
                ),
            }, ensure_ascii=False)

    def _execute_pipeline(self, pipeline: str, params: dict, *, task_id: str) -> dict:
        # Agent and REST must execute the same writer, against the same task id.
        # The older _run_l* helpers below only calculated previews and did not
        # persist the domain artifacts they claimed to create.
        from ..pipeline import PipelineRunner

        pipeline_cls = PipelineRunner.PIPELINES[pipeline]
        result = pipeline_cls(task_id, params, self.mysql).run()
        return {**result, "task_id": task_id}

    def _artifact_candidates_for_pipeline(
        self,
        *,
        task_id: str,
        pipeline: str,
        status: str,
        result: dict,
    ) -> list:
        summary = str((result or {}).get("summary") or PIPELINE_TASKS.get(pipeline, pipeline))
        # A task card is a progress record, not proof of a domain artifact.
        kind_by_pipeline = {name: "pipeline_task" for name in PIPELINE_TASKS}
        return [{
            "kind": kind_by_pipeline.get(pipeline, "pipeline_task"),
            "status": status,
            "title": PIPELINE_TASKS.get(pipeline, "管线任务"),
            "summary": summary,
            "preview": {
                "pipeline": pipeline,
                "task_id": task_id,
                "status": status,
            },
            "actions": [{"id": "confirm_resume", "label": "核对简历"}]
                if pipeline == "L0_parse_resume" and status == "needs_input" else [],
            "resource_type": "pipeline_task",
            "resource_id": task_id,
            "version": 1,
            "verified": status == "completed",
        }]

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

        # Defense in depth for direct/legacy adapters that bypass PreToolUse.
        # Keep the trusted domain L1 runner available; deny only this model-
        # writable dispatcher, before task creation or any graph side effect.
        if pipeline == "L1_build_graph":
            raise PermissionError("Agent 不可执行管理性图谱构建")

        logger.info(f"pipeline_run 执行: pipeline={pipeline}, params={params_str}")

        valid_pipelines = set(PIPELINE_TASKS) - {"L1_build_graph"}
        if pipeline not in valid_pipelines:
            return f"[pipeline_run] 错误: 未知管线 '{pipeline}', 可选: {', '.join(sorted(valid_pipelines))}"

        try:
            params = json.loads(params_str) if isinstance(params_str, str) else params_str
        except json.JSONDecodeError:
            return f"[pipeline_run] 错误: params 不是有效的 JSON 格式"

        if not isinstance(params, dict):
            return "[pipeline_run] 错误: params 必须是 JSON 对象"
        # Never use the model-written params identity as the execution owner.
        # GateContext injects the trusted student_id outside the JSON string.
        trusted_student_id = kwargs.get("student_id")
        if not trusted_student_id:
            return "[pipeline_run] 错误: 缺少服务端 Principal"
        params["student_id"] = trusted_student_id
        if pipeline == "L0_parse_resume" and params.get("file_path"):
            from ...config import Config

            owner_root = os.path.realpath(os.path.join(Config.UPLOAD_FOLDER, trusted_student_id))
            file_path = os.path.realpath(str(params["file_path"]))
            try:
                is_owned_path = os.path.commonpath((owner_root, file_path)) == owner_root
            except ValueError:
                is_owned_path = False
            if not is_owned_path:
                return "[pipeline_run] 错误: 简历文件不属于当前 Principal"

        return self._run_pipeline_sync(pipeline, params)
