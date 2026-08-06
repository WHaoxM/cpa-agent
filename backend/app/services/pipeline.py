"""
L0-L4 Pipeline 调度器
CPA-Agent 数据处理管线入口
"""

import json
import uuid
from abc import ABC, abstractmethod
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

from ..utils.logger import get_logger
from ..services.db.mysql_client import MySQLClient, get_mysql_client

logger = get_logger('cpa-agent.pipeline')


class PipelineLevel(str, Enum):
    L0_PARSE_RESUME = "L0_parse_resume"
    L1_BUILD_GRAPH = "L1_build_graph"
    L2_GENERATE_PORTRAIT = "L2_generate_portrait"
    L3_RUN_MATCH = "L3_run_match"
    L4_GENERATE_REPORT = "L4_generate_report"


class PipelineStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class BasePipeline(ABC):
    """管线基类"""

    def __init__(self, task_id: str, params: Dict[str, Any], mysql: MySQLClient):
        self.task_id = task_id
        self.params = params
        self.mysql = mysql
        self.status = PipelineStatus.PENDING

    @abstractmethod
    def run(self) -> Dict[str, Any]:
        """执行管线"""
        pass

    def update_status(self, status: PipelineStatus, result: Optional[Dict] = None, error: Optional[str] = None):
        """更新任务状态"""
        if status == PipelineStatus.RUNNING:
            self.mysql.execute(
                "UPDATE pipeline_task SET status=:status, started_at=:started_at WHERE task_id=:task_id",
                {"status": status.value, "started_at": datetime.now(), "task_id": self.task_id},
                commit=True
            )
        elif status == PipelineStatus.COMPLETED:
            self.mysql.execute(
                "UPDATE pipeline_task SET status=:status, completed_at=:completed_at, result_json=:result_json WHERE task_id=:task_id",
                {"status": status.value, "completed_at": datetime.now(), "result_json": json.dumps(result), "task_id": self.task_id},
                commit=True
            )
        elif status == PipelineStatus.FAILED:
            self.mysql.execute(
                "UPDATE pipeline_task SET status=:status, error_message=:error_message WHERE task_id=:task_id",
                {"status": status.value, "error_message": error, "task_id": self.task_id},
                commit=True
            )


class L0ParseResumePipeline(BasePipeline):
    """L0: 简历解析管线 — 调用 l0_parser + 写入 MySQL"""

    def run(self) -> Dict[str, Any]:
        logger.info(f"[L0] 开始解析简历: {self.params}")
        self.update_status(PipelineStatus.RUNNING)

        student_id = self.params.get("student_id")
        file_path = self.params.get("file_path")
        text = self.params.get("text")

        try:
            from ..pipeline.entity_extractor.l0_parser import L0ResumeParser

            parser = L0ResumeParser()
            if file_path:
                parse_result = parser.parse_file(file_path)
            elif text:
                parse_result = parser.parse_text(text)
            else:
                raise ValueError("缺少 file_path 或 text 参数")

            if not parse_result.success:
                raise RuntimeError(parse_result.error or "解析失败")

            # 写入 MySQL student_profile
            profile_json = {}
            if hasattr(parse_result, 'entities') and parse_result.entities:
                import json as _json
                profile_json = parse_result.entities if isinstance(parse_result.entities, dict) else {"raw_entities": _json.dumps(parse_result.entities, ensure_ascii=False)}

            raw_text = parse_result.raw_text if hasattr(parse_result, 'raw_text') else ""

            # ── D-10: 全类型实体归一化 (skill/certificate/award/major/position) ──
            if isinstance(profile_json, dict) and profile_json:
                try:
                    from ..pipeline.entity_extractor.profile_normalizer import normalize_profile
                    profile_json = normalize_profile(profile_json, raw_text)
                except Exception as e:
                    logger.warning(f"[L0] Profile 归一化失败(非阻断): {e}")

            self.mysql.execute(
                """INSERT INTO student_profile (student_id, profile_json, updated_at)
                   VALUES (:sid, :pj, NOW())
                   ON DUPLICATE KEY UPDATE profile_json=:pj, updated_at=NOW()""",
                {"sid": student_id, "pj": json.dumps(profile_json, ensure_ascii=False) if isinstance(profile_json, dict) else profile_json},
                commit=True,
            )

            result = {
                "student_id": student_id,
                "block_count": len(parse_result.blocks) if hasattr(parse_result, 'blocks') else 0,
                "profile_json": profile_json,
                "summary": f"简历解析完成，{len(parse_result.blocks) if hasattr(parse_result, 'blocks') else 0} 个文本块"
            }
            self.update_status(PipelineStatus.COMPLETED, result)
            return result
        except Exception as e:
            logger.error(f"[L0] 解析失败: {e}")
            self.update_status(PipelineStatus.FAILED, error=str(e))
            raise


class L1BuildGraphPipeline(BasePipeline):
    """L1: 知识图谱构建管线"""

    def run(self) -> Dict[str, Any]:
        logger.info(f"[L1] 开始构建图谱: {self.params}")
        self.update_status(PipelineStatus.RUNNING)

        student_id = self.params.get("student_id")
        build_type = self.params.get("type", "skill_skeleton")
        position_name = self.params.get("position_name", "未知岗位")

        try:
            from ..pipeline.entity_extractor.graph_storage import GraphStorage
            from ..pipeline.entity_extractor.nsle_pipeline import NSLEPipeline

            graph_storage = GraphStorage()
            nsle_pipeline = NSLEPipeline(use_deepseek=True)

            saved_skills = 0
            saved_relations = 0

            if build_type == "skill_skeleton":
                jd_texts = self.params.get("jd_texts", [])
                all_required_skills = []
                all_preferred_skills = []
                all_bonus_skills = []

                for jd_text in jd_texts:
                    result = nsle_pipeline.run_jd_text(jd_text)
                    if result.get("success"):
                        skills = result.get("required_skills", [])
                        for skill in skills:
                            skill_name = skill if isinstance(skill, str) else skill.get("name", "")
                            if not skill_name:
                                continue
                            skill_info = {
                                "name": skill_name,
                                "category": "IT技能",
                                "tf_score": 0.8,
                                "source": "jd_extraction"
                            }
                            all_required_skills.append(skill_info)

                if all_required_skills:
                    portrait_result = graph_storage.save_job_portrait(
                        position_name=position_name,
                        portrait_id=self.params.get("portrait_id"),
                        required_skills=all_required_skills,
                        preferred_skills=all_preferred_skills if all_preferred_skills else None,
                        bonus_skills=all_bonus_skills if all_bonus_skills else None,
                        jd_count=len(jd_texts)
                    )
                    saved_skills = portrait_result.get("saved_skills", len(all_required_skills))

            elif build_type == "student_skills" and student_id:
                profile_text = self.params.get("profile_text", "")
                if profile_text:
                    result = nsle_pipeline.run_resume_text(profile_text)
                    if result.get("success"):
                        skills = result.get("skills", [])
                        for skill in skills:
                            skill_name = skill if isinstance(skill, str) else skill.get("name", "")
                            if not skill_name:
                                continue
                            skill_dict = {
                                "name": skill_name,
                                "category": "IT技能",
                                "confidence": 0.7,
                                "source": "resume_extraction"
                            }
                            graph_storage.save_skill_with_relation(
                                skill=skill_dict,
                                relation_type="HAS_SKILL",
                                target_entity_id=student_id,
                                target_entity_type="student"
                            )
                            saved_skills += 1

            result = {
                "student_id": student_id,
                "build_type": build_type,
                "position_name": position_name,
                "saved_skills": saved_skills,
                "saved_relations": saved_relations,
                "summary": f"图谱构建完成，保存 {saved_skills} 个技能节点"
            }
            self.update_status(PipelineStatus.COMPLETED, result)
            return result
        except Exception as e:
            logger.error(f"[L1] 构建失败: {e}")
            self.update_status(PipelineStatus.FAILED, error=str(e))
            raise


class L2GeneratePortraitPipeline(BasePipeline):
    """L2: 画像生成管线 — 调用 PortraitGenerator + 写入 MySQL"""

    def run(self) -> Dict[str, Any]:
        logger.info(f"[L2] 开始生成画像: {self.params}")
        self.update_status(PipelineStatus.RUNNING)

        student_id = self.params.get("student_id")
        job_id = self.params.get("job_id")
        skills = self.params.get("skills", [])

        try:
            from ..pipeline.entity_extractor.portrait_generator import PortraitGenerator
            from ..pipeline.entity_extractor.graph_storage import GraphStorage

            # 如果未提供 skills, 从 Neo4j 获取
            if not skills and student_id:
                storage = GraphStorage()
                graph_skills = storage.get_entity_skills(student_id, entity_type="student")
                skills = [{"name": s["name"], "category": s.get("category", ""), "confidence": s.get("confidence", 0.5)} for s in graph_skills]

            if not skills:
                raise RuntimeError("未找到学生技能数据，请先执行 L1 构建")

            # 获取 student_profile
            student_profile = None
            if student_id:
                rows = self.mysql.fetch_all(
                    "SELECT profile_json FROM student_profile WHERE student_id=:sid LIMIT 1",
                    {"sid": student_id}
                )
                if rows and rows[0].get("profile_json"):
                    pj = rows[0]["profile_json"]
                    student_profile = json.loads(pj) if isinstance(pj, str) else pj

            portrait_gen = PortraitGenerator()
            portrait = portrait_gen.generate_student_portrait(
                student_id=student_id or "unknown",
                skills=skills,
                match_job_id=job_id,
                student_profile=student_profile,
            )

            # 写入 MySQL student_portrait_4d (v2.0 Schema)
            summary = portrait.get("summary", {})
            d1_score = portrait.get("D1_basic", {}).get("dimension_score", 0)
            d2_score = portrait.get("D2_skill", {}).get("dimension_score", 0)
            d3_score = portrait.get("D3_quality", {}).get("dimension_score", 0)
            d4_score = portrait.get("D4_potential", {}).get("dimension_score", 0)
            comp_score = summary.get("competitiveness_score", 0)
            skill_cov = portrait.get("D2_skill", {}).get("skill_coverage", 0)

            self.mysql.execute(
                """INSERT INTO student_portrait_4d
                   (student_id, target_job_id, d1_score, d2_score, d3_score, d4_score,
                    competitiveness, skill_coverage, portrait_json, updated_at)
                   VALUES (:sid, :jid, :d1, :d2, :d3, :d4, :comp, :cov, :pj, NOW())
                   ON DUPLICATE KEY UPDATE d1_score=:d1, d2_score=:d2, d3_score=:d3, d4_score=:d4,
                    competitiveness=:comp, skill_coverage=:cov, portrait_json=:pj, updated_at=NOW()""",
                {
                    "sid": student_id, "jid": job_id,
                    "d1": d1_score, "d2": d2_score, "d3": d3_score, "d4": d4_score,
                    "comp": comp_score, "cov": skill_cov,
                    "pj": json.dumps(portrait, ensure_ascii=False),
                },
                commit=True,
            )

            result = {
                "student_id": student_id,
                "job_id": job_id,
                "competitiveness_score": comp_score,
                "competitiveness_level": summary.get("competitiveness_level", ""),
                "completeness_score": summary.get("completeness_score", 0),
                "d1_score": d1_score,
                "d2_score": d2_score,
                "d3_score": d3_score,
                "d4_score": d4_score,
                "skill_coverage": skill_cov,
                "summary": f"画像生成完成，竞争力 {comp_score} ({summary.get('competitiveness_level', '')})"
            }
            self.update_status(PipelineStatus.COMPLETED, result)
            return result
        except Exception as e:
            logger.error(f"[L2] 生成失败: {e}")
            self.update_status(PipelineStatus.FAILED, error=str(e))
            raise


class L3RunMatchPipeline(BasePipeline):
    """L3: 人岗匹配管线 — 调用 SkillMatcher + 写入 MySQL match_result/skill_gap"""

    def run(self) -> Dict[str, Any]:
        logger.info(f"[L3] 开始匹配: {self.params}")
        self.update_status(PipelineStatus.RUNNING)

        student_id = self.params.get("student_id")
        job_portrait_id = self.params.get("job_portrait_id") or self.params.get("job_id")
        student_skills = self.params.get("student_skills", [])
        job_skills = self.params.get("job_skills", [])

        try:
            from ..pipeline.entity_extractor.matcher import SkillMatcher, calculate_match_score
            from ..pipeline.entity_extractor.graph_storage import GraphStorage

            storage = GraphStorage()

            # 获取学生技能
            if not student_skills and student_id:
                graph_skills = storage.get_entity_skills(student_id, entity_type="student")
                student_skills = [{"name": s["name"], "level": s.get("level", "了解"), "confidence": s.get("confidence", 0.5)} for s in graph_skills]

            # 获取岗位技能
            if not job_skills and job_portrait_id:
                try:
                    from ..services.db.neo4j_client import Neo4jClient
                    neo4j = Neo4jClient()
                    job_result = neo4j.execute_query(
                        "MATCH (j:JobPortrait {portrait_id: $pid}) MATCH (j)-[r:REQUIRES|PREFERS|BONUS]->(s:Skill) RETURN s.name as name, r.level as level",
                        {"pid": job_portrait_id}
                    )
                    if job_result:
                        job_skills = [{"name": r["name"], "level": r.get("level", "必须")} for r in job_result]
                except Exception as neo_err:
                    logger.warning(f"Neo4j 查询岗位技能失败, 尝试 MySQL: {neo_err}")

            if not job_skills and job_portrait_id:
                rows = self.mysql.fetch_all(
                    "SELECT portrait_json FROM job_portrait_4d WHERE portrait_id=:pid LIMIT 1",
                    {"pid": job_portrait_id}
                )
                if rows and rows[0].get("portrait_json"):
                    pj = rows[0]["portrait_json"]
                    jp_data = json.loads(pj) if isinstance(pj, str) else pj
                    req_skills = jp_data.get("required_skills", [])
                    job_skills = [{"name": s, "level": "必须"} for s in req_skills]

            if not student_skills or not job_skills:
                raise RuntimeError("学生或岗位技能列表为空，无法匹配")

            # 获取 student_profile
            student_profile = None
            if student_id:
                rows = self.mysql.fetch_all(
                    "SELECT profile_json FROM student_profile WHERE student_id=:sid LIMIT 1",
                    {"sid": student_id}
                )
                if rows and rows[0].get("profile_json"):
                    pj = rows[0]["profile_json"]
                    student_profile = json.loads(pj) if isinstance(pj, str) else pj

            result = calculate_match_score(student_skills, job_skills, student_profile)

            # 写入 MySQL match_result (R-08: 关键技能缺失→不匹配)
            critical_missing = [g["skill"] for g in result.get("gap_priorities", []) if g.get("priority") == "critical"]
            match_result_bool = (
                result["total_score"] >= 50
                and result.get("skill_coverage", 0) >= 0.8
                and len(critical_missing) == 0  # R-08: 关键技能不满足则不匹配
            )
            self.mysql.execute(
                """INSERT INTO match_result
                   (student_id, job_portrait_id, total_score, d1_match, d2_match, d3_match, d4_match,
                    skill_coverage, is_matched, result_json, created_at)
                   VALUES (:sid, :jid, :ts, :d1, :d2, :d3, :d4, :sc, :im, :rj, NOW())
                   ON DUPLICATE KEY UPDATE
                     total_score=:ts, d1_match=:d1, d2_match=:d2, d3_match=:d3, d4_match=:d4,
                     skill_coverage=:sc, is_matched=:im, result_json=:rj""",
                {
                    "sid": student_id, "jid": job_portrait_id,
                    "ts": result["total_score"],
                    "d1": result["d1_score"], "d2": result["d2_score"],
                    "d3": result["d3_score"], "d4": result["d4_score"],
                    "sc": result["skill_coverage"],
                    "im": 1 if match_result_bool else 0,
                    "rj": json.dumps(result, ensure_ascii=False),
                },
                commit=True,
            )

            # 写入 MySQL skill_gap
            gap_data = result.get("gap_priorities", [])
            critical = [g["skill"] for g in gap_data if g.get("priority") == "critical"]
            quick_learn = [g["skill"] for g in gap_data if g.get("priority") == "quick_learn"]
            self.mysql.execute(
                """INSERT INTO skill_gap
                   (student_id, job_portrait_id, critical_count, gap_json, created_at)
                   VALUES (:sid, :jid, :cc, :gj, NOW())
                   ON DUPLICATE KEY UPDATE critical_count=:cc, gap_json=:gj""",
                {
                    "sid": student_id, "jid": job_portrait_id,
                    "cc": len(critical),
                    "gj": json.dumps({"critical": critical, "quick_learn": quick_learn, "missing": result.get("missing_skills", [])}, ensure_ascii=False),
                },
                commit=True,
            )

            # 写入 MySQL career_path (从 Neo4j 获取晋升/换岗路径)
            try:
                # 获取岗位名称
                job_name = self.params.get("target_job_name", "")
                if not job_name and job_portrait_id:
                    jrow = self.mysql.query_one(
                        "SELECT position_name FROM job_portrait_4d WHERE portrait_id=:pid",
                        {"pid": job_portrait_id}
                    )
                    if jrow:
                        job_name = jrow.get("position_name") or ""

                promotion_chains = storage.get_promotion_chains(job_name) if job_name else []
                transfer_options = storage.get_transfer_options(job_name) if job_name else []

                recommended_dir = ""
                if promotion_chains:
                    recommended_dir = "晋升"
                elif transfer_options:
                    recommended_dir = "换岗"

                path_data = {
                    "promotion_chains": promotion_chains[:3],
                    "transfer_options": transfer_options[:5],
                    "recommended_dir": recommended_dir,
                }
                self.mysql.execute(
                    """INSERT INTO career_path
                       (student_id, job_portrait_id, recommended_dir, path_json, created_at)
                       VALUES (:sid, :jid, :rdir, :pj, NOW())
                       ON DUPLICATE KEY UPDATE recommended_dir=:rdir, path_json=:pj""",
                    {
                        "sid": student_id, "jid": job_portrait_id,
                        "rdir": recommended_dir,
                        "pj": json.dumps(path_data, ensure_ascii=False),
                    },
                    commit=True,
                )
                result["career_path"] = path_data
            except Exception as path_err:
                logger.warning(f"[L3] career_path 写入失败 (非致命): {path_err}")

            result["student_id"] = student_id
            result["job_portrait_id"] = job_portrait_id
            result["summary"] = f"匹配完成，总分 {result['total_score']} ({result.get('match_level', '')})"
            self.update_status(PipelineStatus.COMPLETED, result)
            return result
        except Exception as e:
            logger.error(f"[L3] 匹配失败: {e}")
            self.update_status(PipelineStatus.FAILED, error=str(e))
            raise


class L4GenerateReportPipeline(BasePipeline):
    """L4: 报告生成管线 — v2.0 6章内容生成"""

    def run(self) -> Dict[str, Any]:
        logger.info(f"[L4] 开始生成报告: {self.params}")
        self.update_status(PipelineStatus.RUNNING)

        student_id = self.params.get("student_id")
        job_portrait_id = self.params.get("job_portrait_id") or self.params.get("job_id")
        title = self.params.get("title")
        student_skills = self.params.get("student_skills", [])
        student_profile = self.params.get("student_profile")
        target_job_name = self.params.get("target_job_name", "")
        target_job_skills = self.params.get("target_job_skills", [])

        start_time = datetime.now()

        try:
            report_id = f"report_{uuid.uuid4().hex[:12]}"

            # 创建记录 (status=generating)
            self.mysql.execute(
                """INSERT INTO career_report
                   (report_id, student_id, job_portrait_id, title, status, created_at, updated_at)
                   VALUES (:rid, :sid, :jid, :title, :status, :cat, :uat)""",
                {"rid": report_id, "sid": student_id, "jid": job_portrait_id,
                 "title": title or f"职业发展报告 - {student_id}", "status": "generating",
                 "cat": start_time, "uat": start_time},
                commit=True,
            )

            # 从 MySQL 获取学生画像和技能 (如果未传入)
            if not student_skills:
                row = self.mysql.query_one(
                    "SELECT portrait_json FROM student_portrait_4d WHERE student_id=:sid",
                    {"sid": student_id}
                )
                if row:
                    pj = row.get("portrait_json")
                    portrait_data = json.loads(pj) if isinstance(pj, str) else pj
                    student_skills = portrait_data.get("D2_skill", {}).get("skill_list", [])
                    student_profile = portrait_data

            # 生成6章报告
            from app.pipeline.entity_extractor.report_generator import ReportGenerator
            rg = ReportGenerator()
            report = rg.generate_career_report(
                student_id=student_id,
                student_skills=student_skills,
                student_profile=student_profile,
                target_job_id=job_portrait_id,
                target_job_name=target_job_name,
                target_job_skills=target_job_skills,
            )

            # 写入 ch1~ch6_json + report_json
            elapsed = (datetime.now() - start_time).total_seconds()
            self.mysql.execute(
                """UPDATE career_report
                   SET status=:status, updated_at=:uat, elapsed_seconds=:elapsed,
                       ch1_json=:ch1, ch2_json=:ch2, ch3_json=:ch3,
                       ch4_json=:ch4, ch5_json=:ch5, ch6_json=:ch6,
                       report_json=:rj
                   WHERE report_id=:rid""",
                {
                    "status": "completed", "uat": datetime.now(), "elapsed": elapsed,
                    "ch1": json.dumps(report.get("ch1_match_analysis", {}), ensure_ascii=False),
                    "ch2": json.dumps(report.get("ch2_career_target", {}), ensure_ascii=False),
                    "ch3": json.dumps(report.get("ch3_action_plan", {}), ensure_ascii=False),
                    "ch4": json.dumps(report.get("ch4_gap_analysis", {}), ensure_ascii=False),
                    "ch5": json.dumps(report.get("ch5_evaluation", {}), ensure_ascii=False),
                    "ch6": json.dumps(report.get("ch6_recommendations", {}), ensure_ascii=False),
                    "rj": json.dumps(report, ensure_ascii=False),
                    "rid": report_id,
                },
                commit=True,
            )

            result = {
                "report_id": report_id, "student_id": student_id,
                "job_portrait_id": job_portrait_id,
                "competitiveness_score": report.get("portrait_summary", {}).get("competitiveness_score", 0),
                "competitiveness_level": report.get("portrait_summary", {}).get("competitiveness_level", ""),
                "summary": f"报告生成完成，竞争力 {report.get('portrait_summary', {}).get('competitiveness_score', 0)}"
            }
            self.update_status(PipelineStatus.COMPLETED, result)
            return result
        except Exception as e:
            logger.error(f"[L4] 生成失败: {e}")
            self.update_status(PipelineStatus.FAILED, error=str(e))

            if report_id:
                self.mysql.execute(
                    """
                    UPDATE career_report
                    SET status = :status,
                        report_json = :report_json,
                        updated_at = :updated_at
                    WHERE report_id = :report_id
                    """,
                    {
                        "status": "failed",
                        "report_json": json.dumps(
                            {"error": str(e)}, ensure_ascii=False
                        ),
                        "updated_at": datetime.now(),
                        "report_id": report_id,
                    },
                    commit=True,
                )

            raise


class PipelineRunner:
    """管线调度器"""

    PIPELINES = {
        PipelineLevel.L0_PARSE_RESUME: L0ParseResumePipeline,
        PipelineLevel.L1_BUILD_GRAPH: L1BuildGraphPipeline,
        PipelineLevel.L2_GENERATE_PORTRAIT: L2GeneratePortraitPipeline,
        PipelineLevel.L3_RUN_MATCH: L3RunMatchPipeline,
        PipelineLevel.L4_GENERATE_REPORT: L4GenerateReportPipeline,
    }

    def __init__(self):
        self.mysql = get_mysql_client()

    def run(self, pipeline: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """执行管线"""
        if pipeline not in self.PIPELINES:
            raise ValueError(f"未知管线: {pipeline}")

        task_id = f"task_{uuid.uuid4().hex[:12]}"

        self.mysql.execute(
            """
            INSERT INTO pipeline_task
            (task_id, pipeline, status, params_json, student_id, job_id, triggered_by, created_at)
            VALUES (:task_id, :pipeline, :status, :params_json, :student_id, :job_id, :triggered_by, :created_at)
            """,
            {
                "task_id": task_id,
                "pipeline": pipeline,
                "status": "pending",
                "params_json": json.dumps(params),
                "student_id": params.get("student_id"),
                "job_id": params.get("job_id"),
                "triggered_by": "api",
                "created_at": datetime.now(),
            },
            commit=True
        )

        pipeline_cls = self.PIPELINES[pipeline]
        runner = pipeline_cls(task_id, params, self.mysql)

        return runner.run()


__all__ = [
    'PipelineLevel',
    'PipelineStatus', 
    'BasePipeline',
    'PipelineRunner',
]
