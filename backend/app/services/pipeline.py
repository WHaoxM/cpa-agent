"""
L0-L4 Pipeline 调度器
CPA-Agent 数据处理管线入口
"""

import hashlib
import json
import math
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
    NEEDS_INPUT = "needs_input"
    PARTIAL = "partial"


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
        elif status in (PipelineStatus.COMPLETED, PipelineStatus.NEEDS_INPUT, PipelineStatus.PARTIAL):
            self.mysql.execute(
                "UPDATE pipeline_task SET status=:status, completed_at=:completed_at, result_json=:result_json WHERE task_id=:task_id",
                {"status": status.value, "completed_at": datetime.now() if status == PipelineStatus.COMPLETED else None,
                 "result_json": json.dumps(result), "task_id": self.task_id},
                commit=True
            )
        elif status == PipelineStatus.FAILED:
            self.mysql.execute(
                "UPDATE pipeline_task SET status=:status, error_message=:error_message WHERE task_id=:task_id",
                {"status": status.value, "error_message": error, "task_id": self.task_id},
                commit=True
            )


class L0ParseResumePipeline(BasePipeline):
    """Shared REST/Agent parser; candidates are not confirmed student profiles."""

    def run(self) -> Dict[str, Any]:
        logger.info(f"[L0] 开始解析简历: {self.params}")
        self.update_status(PipelineStatus.RUNNING)

        student_id = self.params.get("student_id")
        file_path = self.params.get("file_path")
        text = self.params.get("text")

        try:
            from ..pipeline.entity_extractor.l0_full_parser import L0FullParser
            from ..pipeline.entity_extractor.nsle_pipeline import NSLEPipeline

            if not student_id:
                raise ValueError("缺少服务端身份")
            parser = L0FullParser()
            if file_path:
                parse_result = parser.parse_file(file_path)
            elif text:
                parse_result = parser.parse_text(text)
            else:
                raise ValueError("缺少 file_path 或 text 参数")

            if not parse_result.success:
                raise RuntimeError(parse_result.error or "解析失败")

            raw_text = parse_result.text
            if not raw_text.strip():
                raise ValueError("未提取到简历正文")
            structured = NSLEPipeline(use_deepseek=True).run_resume_text(raw_text)
            if not structured.get("success"):
                raise RuntimeError("简历结构化失败；未修改已确认资料")
            candidate = structured.get("student_profile") or {}
            validation = structured.get("structure_validation") or {"ok": False}
            blocks = [{"block_id": b.block_id, "text": b.text,
                       "page": b.page + 1, "block_type": b.block_type}
                      for b in parse_result.blocks]
            metadata = parse_result.metadata or {}
            page_count = metadata.get("pages") or metadata.get("page_count")
            observed = sorted({b["page"] for b in blocks if b["text"].strip()})
            missing = [p for p in range(1, int(page_count) + 1) if p not in observed] if page_count else []
            status = PipelineStatus.NEEDS_INPUT if validation.get("ok") is True and candidate else PipelineStatus.PARTIAL

            result = {
                "student_id": student_id,
                "task_id": self.task_id,
                "status": status.value,
                "profile_ready": False,
                "candidate_profile": candidate,
                "structure_validation": validation,
                "block_count": len(blocks),
                "source_evidence": {"task_id": self.task_id, "file_id": self.params.get("file_id"),
                    "blocks": blocks, "page_count": page_count, "observed_pages": observed,
                    "missing_pages": missing, "engine": parse_result.engine,
                    "coverage": "partial" if missing else "unverified",
                    "accuracy_verified": False},
                "next_action": "confirm_resume" if status == PipelineStatus.NEEDS_INPUT else "repair_resume_structure",
                "summary": "简历候选已保存，需用户核对确认；尚未生成画像" if status == PipelineStatus.NEEDS_INPUT else "简历结构化不完整，请补充或重新解析；未修改已确认资料",
            }
            self.update_status(status, result)
            return result
        except Exception as e:
            logger.error(f"[L0] 解析失败: {e}")
            self.update_status(PipelineStatus.FAILED, error=str(e))
            raise


def require_confirmed_profile(mysql, student_id: str) -> dict:
    """Never accept a model-written `confirmed` parameter as user consent."""
    rows = mysql.fetch_all("SELECT profile_json FROM student_profile WHERE student_id=:sid LIMIT 1", {"sid": student_id})
    profile = rows[0].get("profile_json") if rows else None
    profile = json.loads(profile) if isinstance(profile, str) else profile
    confirmation = profile.get("_resume_confirmation") if isinstance(profile, dict) else None
    if not isinstance(confirmation, dict) or not confirmation.get("task_id") or confirmation.get("student_id") != student_id:
        raise ValueError("请先核对并确认简历候选，再生成画像、匹配或报告")
    tasks = mysql.fetch_all("SELECT task_id, student_id, pipeline, status, result_json FROM pipeline_task WHERE task_id=:task_id LIMIT 1",
                            {"task_id": confirmation["task_id"]})
    task = tasks[0] if tasks else {}
    result = task.get("result_json") or {}
    result = json.loads(result) if isinstance(result, str) else result
    if (task.get("student_id") != student_id or task.get("pipeline") != PipelineLevel.L0_PARSE_RESUME
            or task.get("status") != "completed" or result.get("confirmed") is not True
            or result.get("candidate_profile") != {k: v for k, v in profile.items() if k != "_resume_confirmation"}):
        raise ValueError("简历确认来源未通过校验，请重新核对确认")
    return profile


class PipelineSourceUnavailable(Exception):
    """An unmet business prerequisite, not a successful computation or a crash."""

    def __init__(self, reason: str, next_action: str, message: str, sources=None):
        super().__init__(message)
        self.reason = reason
        self.next_action = next_action
        self.sources = sources or {}


def require_authoritative_match_sources(mysql, student_id: str, job_portrait_id: str,
                                        confirmed_profile: dict) -> dict:
    """Verify persisted L2 provenance before any scoring or report generation.

    Only the server-owned publication repository can confer catalog authority.
    A flag in legacy job JSON or tool input never upgrades its trust level.
    """
    confirmation_id = confirmed_profile["_resume_confirmation"]["task_id"]
    sources = {"source_confirmation_task_id": confirmation_id}
    rows = mysql.fetch_all(
        "SELECT id, student_id, target_job_id, portrait_json FROM student_portrait_4d "
        "WHERE student_id=:sid AND (target_job_id=:jid OR target_job_id IS NULL) "
        "ORDER BY (target_job_id=:jid) DESC, id DESC LIMIT 1",
        {"sid": student_id, "jid": job_portrait_id})
    row = rows[0] if rows else {}
    portrait = row.get("portrait_json") or {}
    portrait = json.loads(portrait) if isinstance(portrait, str) else portrait
    source_task_id = portrait.get("source_portrait_task_id") if isinstance(portrait, dict) else None
    valid = (row.get("id") and row.get("student_id") == student_id
             and row.get("target_job_id") in (None, job_portrait_id)
             and isinstance(portrait, dict)
             and portrait.get("source_confirmation_task_id") == confirmation_id
             and source_task_id)
    tasks = mysql.fetch_all(
        "SELECT task_id, student_id, pipeline, status, result_json FROM pipeline_task "
        "WHERE task_id=:task_id LIMIT 1", {"task_id": source_task_id}) if valid else []
    task = tasks[0] if tasks else {}
    payload = task.get("result_json") or {}
    payload = json.loads(payload) if isinstance(payload, str) else payload
    valid = (valid and task.get("student_id") == student_id
             and task.get("pipeline") == PipelineLevel.L2_GENERATE_PORTRAIT
             and task.get("status") == "completed" and isinstance(payload, dict)
             and str(payload.get("portrait_id")) == str(row["id"])
             and payload.get("portrait") == portrait)
    if not valid:
        raise PipelineSourceUnavailable("confirmed_portrait_unavailable", "generate_portrait",
            "缺少与当前已确认简历一致的已落库 L2 画像，请先生成画像", sources)
    sources.update({"source_portrait_id": str(row["id"]),
                    "source_portrait_task_id": source_task_id,
                    "source_portrait_sha256": hashlib.sha256(json.dumps(
                        portrait, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                    ).encode("utf-8")).hexdigest()})
    from .job_publication import read_published_job
    publication = read_published_job(mysql, job_portrait_id)
    if not publication:
        raise PipelineSourceUnavailable("catalog_publication_unavailable", "publish_verified_job_catalog",
            "岗位目录尚无可验证的服务端发布来源；未计算匹配或生成报告", sources)
    sources.update({"source_publication_id": str(publication["id"]),
                    "source_publication_version": publication["publication_version"],
                    "source_publication_sha256": publication["content_sha256"]})
    from .job_publication import scoring_readiness
    missing = scoring_readiness(publication["snapshot_json"])
    if missing:
        sources["missing_job_fields"] = missing
        raise PipelineSourceUnavailable("catalog_requirements_unknown", "review_job_requirements",
            "岗位来源有未核实的硬约束或技能，不能交给旧评分器按默认值计算", sources)
    return {"references": sources, "portrait": portrait, "publication": publication}


def source_blocked_result(pipeline: BasePipeline, error: PipelineSourceUnavailable) -> dict:
    result = {"student_id": pipeline.params.get("student_id"), "task_id": pipeline.task_id,
              "status": "partial", "verified": False, "reason": error.reason,
              "next_action": error.next_action, "summary": str(error),
              "resource_type": "pipeline_task", "resource_id": pipeline.task_id,
              **error.sources}
    pipeline.update_status(PipelineStatus.PARTIAL, result)
    return result


def confirm_resume_candidate(mysql, *, student_id: str, task_id: str, acknowledge_partial: bool = False) -> dict:
    if isinstance(mysql, MySQLClient):
        from sqlalchemy import text
        from .pipeline_events import ConfirmationTransaction
        with mysql.engine.begin() as conn:
            # Serialize different candidates for the same owner as well as retries.
            # A first-time placeholder is invisible until commit and rolled back on failure.
            conn.execute(text("INSERT INTO student_profile(student_id, profile_json) "
                              "VALUES (:sid, '{}') ON DUPLICATE KEY UPDATE student_id=student_id"),
                         {"sid": student_id})
            conn.execute(text("SELECT task_id FROM pipeline_task WHERE task_id=:tid "
                              "AND student_id=:sid FOR UPDATE"), {"tid": task_id, "sid": student_id})
            result = _confirm_resume_candidate(ConfirmationTransaction(conn), student_id=student_id,
                task_id=task_id, acknowledge_partial=acknowledge_partial)
        # Commit confirmation and its journal event BEFORE attempting broker
        # delivery. Broker/Neo4j failure must never undo successful consent.
        try:
            from .personal_graph_jobs import request_personal_graph_projection
            result["personal_graph_projection"] = request_personal_graph_projection(
                mysql, student_id=student_id, task_id=task_id)
        except Exception:
            # The completed L0 task + journal is itself the durable pending
            # record; replay discovers absent state after crash/import/DB errors.
            logger.exception("已确认简历；个人图谱投影等待持久重放")
            result["personal_graph_projection"] = {"status": "pending", "retryable": True}
        return result
    # In-memory repositories used by unit tests own their transaction semantics.
    return _confirm_resume_candidate(mysql, student_id=student_id, task_id=task_id,
                                     acknowledge_partial=acknowledge_partial)


def _confirm_resume_candidate(mysql, *, student_id: str, task_id: str, acknowledge_partial: bool = False) -> dict:
    """Explicit user API only. Persist one server-owned candidate, never request-body profile data."""
    rows = mysql.fetch_all(
        "SELECT task_id, student_id, pipeline, status, result_json FROM pipeline_task WHERE task_id=:task_id LIMIT 1",
        {"task_id": task_id})
    if not rows or rows[0].get("student_id") != student_id:
        raise LookupError("简历候选不存在")
    task = rows[0]
    if task.get("pipeline") == PipelineLevel.L0_PARSE_RESUME and task.get("status") == "completed":
        current = require_confirmed_profile(mysql, student_id)
        if current["_resume_confirmation"]["task_id"] != task_id:
            raise ValueError("该候选已被后续确认替代，请重新解析")
        return {"student_id": student_id, "task_id": task_id, "status": "completed",
                "profile_ready": True, "portrait_ready": False, "resource_type": "student_profile",
                "resource_id": student_id, "next_action": "generate_portrait"}
    if task.get("pipeline") != PipelineLevel.L0_PARSE_RESUME or task.get("status") != PipelineStatus.NEEDS_INPUT:
        raise ValueError("此任务不是可确认的简历候选")
    result = task.get("result_json")
    result = json.loads(result) if isinstance(result, str) else result
    candidate = (result or {}).get("candidate_profile")
    if not isinstance(candidate, dict) or not candidate or (result.get("structure_validation") or {}).get("ok") is not True:
        raise ValueError("简历结构化不完整，请补充后重新解析")
    evidence = result.get("source_evidence") or {}
    if evidence.get("missing_pages") and not acknowledge_partial:
        raise ValueError("存在未提取页面，请修复或明确确认仅使用已提取内容")
    profile = {**candidate, "_resume_confirmation": {
        "task_id": task_id, "student_id": student_id, "confirmed_at": datetime.now().isoformat(),
        "source_evidence": evidence, "partial_acknowledged": bool(acknowledge_partial)}}
    mysql.execute(
        """INSERT INTO student_profile (student_id, profile_json, updated_at)
           VALUES (:sid, :pj, NOW()) ON DUPLICATE KEY UPDATE profile_json=:pj, updated_at=NOW()""",
        {"sid": student_id, "pj": json.dumps(profile, ensure_ascii=False)}, commit=True)
    result = {**result, "confirmed": True, "status": "completed", "profile_ready": True,
              "next_action": "generate_portrait", "summary": "简历资料已由用户确认；画像尚未生成"}
    mysql.execute("UPDATE pipeline_task SET status=:status, result_json=:result_json, completed_at=:completed_at WHERE task_id=:task_id AND student_id=:student_id",
                  {"status": "completed", "result_json": json.dumps(result, ensure_ascii=False),
                   "completed_at": datetime.now(), "task_id": task_id, "student_id": student_id}, commit=True)
    # Read both authoritative rows before returning a usable profile reference.
    persisted = require_confirmed_profile(mysql, student_id)
    if persisted["_resume_confirmation"]["task_id"] != task_id:
        raise RuntimeError("简历确认写入未通过回读验证")
    return {"student_id": student_id, "task_id": task_id, "status": "completed",
            "profile_ready": True, "portrait_ready": False, "resource_type": "student_profile",
            "resource_id": student_id, "next_action": "generate_portrait"}


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

            if not saved_skills:
                raise RuntimeError("未写入任何技能节点，图谱构建未完成")

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
            confirmed_profile = require_confirmed_profile(self.mysql, student_id)
            from ..pipeline.entity_extractor.portrait_generator import PortraitGenerator
            from ..pipeline.entity_extractor.graph_storage import GraphStorage

            # Only the confirmed profile supplies student facts; model parameters
            # are not a second source of authority.
            skills = [{"name": s} if isinstance(s, str) else s
                      for s in confirmed_profile.get("skills", []) if isinstance(s, (str, dict))]
            if not skills:
                raise ValueError("已确认简历没有技能，请补充后重新确认")

            # 获取 student_profile
            student_profile = confirmed_profile

            portrait_gen = PortraitGenerator()
            portrait = portrait_gen.generate_student_portrait(
                student_id=student_id or "unknown",
                skills=skills,
                match_job_id=job_id,
                student_profile=student_profile,
            )
            portrait["source_confirmation_task_id"] = confirmed_profile["_resume_confirmation"]["task_id"]
            portrait["source_portrait_task_id"] = self.task_id

            # The legacy score columns are NOT NULL DEFAULT 0. Do not coerce
            # unknown semantic facts to a fabricated zero or a completed row.
            summary = portrait.get("summary", {})
            d1_score = portrait.get("D1_basic", {}).get("dimension_score")
            d2_score = portrait.get("D2_skill", {}).get("dimension_score")
            d3_score = portrait.get("D3_quality", {}).get("dimension_score")
            d4_score = portrait.get("D4_potential", {}).get("dimension_score")
            comp_score = summary.get("competitiveness_score")
            scores = (d1_score, d2_score, d3_score, d4_score, comp_score)
            if portrait.get("status") == "partial" or not all(
                isinstance(score, (int, float)) and not isinstance(score, bool) and math.isfinite(score)
                for score in scores
            ):
                result = {
                    "status": "partial", "resource_type": "pipeline_task",
                    "resource_id": self.task_id, "portrait_id": None,
                    "source_confirmation_task_id": portrait["source_confirmation_task_id"],
                    "portrait_candidate": portrait,
                    "missing_fields": portrait.get("missing_fields") or [],
                    "next_action": "confirm_semantic_facts",
                    # No semantic-fact review writer is currently exposed. Keep
                    # the machine-readable missing-fact code, but do not imply
                    # that a user can complete an unavailable recovery action.
                    "action_available": False,
                    "resume_state": "not_available",
                    "summary": "画像所需事实或分数缺证据，已保留候选；未写入完成画像",
                }
                self.update_status(PipelineStatus.PARTIAL, result)
                return result

            # 写入 MySQL student_portrait_4d (v2.0 Schema)
            skill_cov = portrait.get("D2_skill", {}).get("skill_coverage", 0)

            self.mysql.execute(
                """INSERT INTO student_portrait_4d
                   (student_id, target_job_id, d1_score, d2_score, d3_score, d4_score,
                    competitiveness, competitiveness_lvl, completeness, skill_count,
                    skill_coverage, portrait_json, updated_at)
                   VALUES (:sid, :jid, :d1, :d2, :d3, :d4, :comp, :lvl, :complete, :count, :cov, :pj, NOW())
                   ON DUPLICATE KEY UPDATE d1_score=:d1, d2_score=:d2, d3_score=:d3, d4_score=:d4,
                    competitiveness=:comp, competitiveness_lvl=:lvl, completeness=:complete, skill_count=:count,
                    skill_coverage=:cov, portrait_json=:pj, updated_at=NOW()""",
                {
                    "sid": student_id, "jid": job_id,
                    "d1": d1_score, "d2": d2_score, "d3": d3_score, "d4": d4_score,
                    "comp": comp_score, "cov": skill_cov,
                    "lvl": summary.get("competitiveness_level", ""),
                    "complete": summary.get("completeness_score", 0), "count": len(skills),
                    "pj": json.dumps(portrait, ensure_ascii=False),
                },
                commit=True,
            )
            records = self.mysql.fetch_all(
                "SELECT id, portrait_json FROM student_portrait_4d WHERE student_id=:sid AND target_job_id <=> :jid ORDER BY id DESC LIMIT 1",
                {"sid": student_id, "jid": job_id})
            saved = records[0] if records else {}
            saved_payload = saved.get("portrait_json") or {}
            saved_payload = json.loads(saved_payload) if isinstance(saved_payload, str) else saved_payload
            if not saved.get("id") or saved_payload.get("source_confirmation_task_id") != portrait["source_confirmation_task_id"]:
                raise RuntimeError("画像写入未通过权威记录回读验证")

            result = {
                "status": "completed", "resource_type": "student_portrait_4d",
                "resource_id": str(saved["id"]), "portrait_id": str(saved["id"]),
                "source_confirmation_task_id": portrait["source_confirmation_task_id"],
                "portrait": portrait,
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
    """Confirmed portrait and operator-published job -> one persisted match."""
    def run(self):
        self.update_status(PipelineStatus.RUNNING)
        try:
            profile = require_confirmed_profile(self.mysql, self.params.get("student_id"))
            sources = require_authoritative_match_sources(
                self.mysql, self.params.get("student_id"),
                self.params.get("job_portrait_id") or self.params.get("job_id"), profile)
            from .verified_match_report import persist_match
            result = persist_match(self, profile, sources)
            self.update_status(PipelineStatus(result["status"]), result)
            return result
        except PipelineSourceUnavailable as exc:
            return source_blocked_result(self, exc)
        except Exception as exc:
            self.update_status(PipelineStatus.FAILED, error=str(exc))
            raise


class L4GenerateReportPipeline(BasePipeline):
    """Render persisted evidence, never rescore or invent recommendations."""
    def run(self):
        self.update_status(PipelineStatus.RUNNING)
        try:
            profile = require_confirmed_profile(self.mysql, self.params.get("student_id"))
            sources = require_authoritative_match_sources(
                self.mysql, self.params.get("student_id"),
                self.params.get("job_portrait_id") or self.params.get("job_id"), profile)
            from .verified_match_report import persist_report
            result = persist_report(self, sources)
            self.update_status(PipelineStatus.PARTIAL, result)
            return result
        except PipelineSourceUnavailable as exc:
            return source_blocked_result(self, exc)
        except Exception as exc:
            self.update_status(PipelineStatus.FAILED, error=str(exc))
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

        result = runner.run()
        return {**result, "task_id": task_id}


__all__ = [
    'PipelineLevel',
    'PipelineStatus', 
    'BasePipeline',
    'PipelineRunner',
]
