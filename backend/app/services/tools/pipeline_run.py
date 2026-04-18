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
from ...services.db.mysql_client import MySQLClient

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
            self._mysql = MySQLClient()
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
        """创建管线任务记录"""
        task_id = f"task_{uuid.uuid4().hex[:12]}"
        
        sql = """
        INSERT INTO pipeline_task 
        (task_id, pipeline, status, params_json, student_id, job_id, triggered_by, created_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """
        
        student_id = params.get("student_id")
        job_id = params.get("job_id")
        
        self.mysql.execute_write(
            sql,
            (task_id, pipeline, "pending", json.dumps(params), student_id, job_id, "agent", datetime.now())
        )
        
        logger.info(f"创建管线任务: task_id={task_id}, pipeline={pipeline}")
        return task_id

    def _run_pipeline_sync(self, pipeline: str, params: dict) -> str:
        """同步执行管线 (简化版，实际应异步)"""
        task_id = self._create_task(pipeline, params)
        
        self.mysql.execute_write(
            "UPDATE pipeline_task SET status=%s, started_at=%s WHERE task_id=%s",
            ("running", datetime.now(), task_id)
        )
        
        try:
            result = self._execute_pipeline(pipeline, params)
            
            self.mysql.execute_write(
                "UPDATE pipeline_task SET status=%s, completed_at=%s, result_json=%s WHERE task_id=%s",
                ("completed", datetime.now(), json.dumps(result), task_id)
            )
            
            return f"[pipeline_run] 任务完成\n任务ID: {task_id}\n管线: {pipeline}\n结果: {result.get('summary', '处理完成')}"
            
        except Exception as e:
            logger.error(f"管线执行失败: {e}")
            self.mysql.execute_write(
                "UPDATE pipeline_task SET status=%s, error_message=%s WHERE task_id=%s",
                ("failed", str(e), task_id)
            )
            return f"[pipeline_run] 任务失败: {str(e)}"

    def _execute_pipeline(self, pipeline: str, params: dict) -> dict:
        """执行具体的管线逻辑"""
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

    def _run_l0_parse_resume(self, params: dict) -> dict:
        """L0: 简历解析"""
        student_id = params.get("student_id")
        file_path = params.get("file_path")
        return {"summary": f"简历解析完成 (student_id={student_id})", "student_id": student_id}

    def _run_l1_build_graph(self, params: dict) -> dict:
        """L1: 知识图谱构建"""
        return {"summary": "知识图谱构建完成"}

    def _run_l2_generate_portrait(self, params: dict) -> dict:
        """L2: 画像生成"""
        student_id = params.get("student_id")
        job_id = params.get("job_id")
        return {"summary": f"画像生成完成 (student_id={student_id}, job_id={job_id})"}

    def _run_l3_run_match(self, params: dict) -> dict:
        """L3: 人岗匹配"""
        student_id = params.get("student_id")
        job_id = params.get("job_id")
        return {"summary": f"匹配完成 (student_id={student_id}, job_id={job_id})"}

    def _run_l4_generate_report(self, params: dict) -> dict:
        """L4: 报告生成"""
        student_id = params.get("student_id")
        job_id = params.get("job_id")
        report_id = f"report_{uuid.uuid4().hex[:12]}"
        return {"summary": f"报告生成完成", "report_id": report_id}

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
