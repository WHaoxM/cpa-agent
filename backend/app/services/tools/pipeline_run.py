"""
Tool 4: pipeline_run — L0-L4 管线触发
智能体自主决定何时触发数据处理管线 (简历解析/画像生成/匹配/报告)
"""

from typing import Dict

from .base import BaseTool
from ...utils.logger import get_logger

logger = get_logger('cpa-agent.tool.pipeline_run')


class PipelineRunTool(BaseTool):
    """L0-L4 管线触发工具"""

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

    def execute(self, **kwargs) -> str:
        pipeline = kwargs.get("pipeline", "")
        params = kwargs.get("params", "{}")

        logger.info(f"pipeline_run 执行: pipeline={pipeline}, params={params}")

        valid_pipelines = {
            "L0_parse_resume", "L1_build_graph", "L2_generate_portrait",
            "L3_run_match", "L4_generate_report"
        }

        if pipeline not in valid_pipelines:
            return f"[pipeline_run] 错误: 未知管线 '{pipeline}', 可选: {', '.join(sorted(valid_pipelines))}"

        # TODO: 实现管线触发
        # 1. 解析 params (student_id, job_id 等)
        # 2. 异步触发对应管线 (threading / Celery)
        # 3. 返回 task_id + status

        return f"[pipeline_run] 管线: {pipeline}\n暂未实现, 待接入管线调度器"
