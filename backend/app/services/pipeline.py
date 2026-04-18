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
from ..services.db.mysql_client import MySQLClient

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
            self.mysql.execute_write(
                "UPDATE pipeline_task SET status=%s, started_at=%s WHERE task_id=%s",
                (status.value, datetime.now(), self.task_id)
            )
        elif status == PipelineStatus.COMPLETED:
            self.mysql.execute_write(
                "UPDATE pipeline_task SET status=%s, completed_at=%s, result_json=%s WHERE task_id=%s",
                (status.value, datetime.now(), json.dumps(result), self.task_id)
            )
        elif status == PipelineStatus.FAILED:
            self.mysql.execute_write(
                "UPDATE pipeline_task SET status=%s, error_message=%s WHERE task_id=%s",
                (status.value, error, self.task_id)
            )


class L0ParseResumePipeline(BasePipeline):
    """L0: 简历解析管线"""

    def run(self) -> Dict[str, Any]:
        logger.info(f"[L0] 开始解析简历: {self.params}")
        self.update_status(PipelineStatus.RUNNING)

        student_id = self.params.get("student_id")
        file_path = self.params.get("file_path")

        try:
            result = {
                "student_id": student_id,
                "profile_json": {},
                "summary": "简历解析完成"
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

        try:
            result = {"summary": "图谱构建完成"}
            self.update_status(PipelineStatus.COMPLETED, result)
            return result
        except Exception as e:
            logger.error(f"[L1] 构建失败: {e}")
            self.update_status(PipelineStatus.FAILED, error=str(e))
            raise


class L2GeneratePortraitPipeline(BasePipeline):
    """L2: 画像生成管线"""

    def run(self) -> Dict[str, Any]:
        logger.info(f"[L2] 开始生成画像: {self.params}")
        self.update_status(PipelineStatus.RUNNING)

        student_id = self.params.get("student_id")
        job_id = self.params.get("job_id")

        try:
            result = {
                "student_id": student_id,
                "job_id": job_id,
                "summary": "画像生成完成"
            }
            self.update_status(PipelineStatus.COMPLETED, result)
            return result
        except Exception as e:
            logger.error(f"[L2] 生成失败: {e}")
            self.update_status(PipelineStatus.FAILED, error=str(e))
            raise


class L3RunMatchPipeline(BasePipeline):
    """L3: 人岗匹配管线"""

    def run(self) -> Dict[str, Any]:
        logger.info(f"[L3] 开始匹配: {self.params}")
        self.update_status(PipelineStatus.RUNNING)

        student_id = self.params.get("student_id")
        job_id = self.params.get("job_id")

        try:
            result = {
                "student_id": student_id,
                "job_id": job_id,
                "summary": "匹配完成"
            }
            self.update_status(PipelineStatus.COMPLETED, result)
            return result
        except Exception as e:
            logger.error(f"[L3] 匹配失败: {e}")
            self.update_status(PipelineStatus.FAILED, error=str(e))
            raise


class L4GenerateReportPipeline(BasePipeline):
    """L4: 报告生成管线"""

    def run(self) -> Dict[str, Any]:
        logger.info(f"[L4] 开始生成报告: {self.params}")
        self.update_status(PipelineStatus.RUNNING)

        student_id = self.params.get("student_id")
        job_id = self.params.get("job_id")

        try:
            report_id = f"report_{uuid.uuid4().hex[:12]}"
            result = {
                "report_id": report_id,
                "student_id": student_id,
                "job_id": job_id,
                "summary": "报告生成完成"
            }
            self.update_status(PipelineStatus.COMPLETED, result)
            return result
        except Exception as e:
            logger.error(f"[L4] 生成失败: {e}")
            self.update_status(PipelineStatus.FAILED, error=str(e))
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
        self.mysql = MySQLClient()

    def run(self, pipeline: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """执行管线"""
        if pipeline not in self.PIPELINES:
            raise ValueError(f"未知管线: {pipeline}")

        task_id = f"task_{uuid.uuid4().hex[:12]}"
        
        self.mysql.execute_write(
            """
            INSERT INTO pipeline_task 
            (task_id, pipeline, status, params_json, student_id, job_id, triggered_by, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (task_id, pipeline, "pending", json.dumps(params), 
             params.get("student_id"), params.get("job_id"), "api", datetime.now())
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
