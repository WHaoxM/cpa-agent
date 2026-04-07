"""
Tool 5: llm_reason — LLM 自然语言推理
职业建议、报告润色、匹配分数解释等需要 LLM 深度思考的场景
"""

from typing import Dict

from .base import BaseTool
from ...utils.logger import get_logger

logger = get_logger('cpa-agent.tool.llm_reason')


class LlmReasonTool(BaseTool):
    """LLM 自然语言推理工具"""

    @property
    def name(self) -> str:
        return "llm_reason"

    @property
    def description(self) -> str:
        return """\
【LLM 推理 — 自然语言分析与建议】
调用 LLM 进行深度推理、分析和建议生成。适用于无法通过数据库查询直接回答的问题。

【使用场景】
- "给我一些职业发展建议"
- "解释一下我的匹配分数为什么这么低"
- "润色报告第三章"
- "分析我的技能优劣势"
- "对比两个岗位哪个更适合我"

【注意】
- 此工具会消耗额外的 LLM 调用, 仅在需要深度分析时使用
- 优先使用其他工具获取事实数据, 再用此工具做综合分析

【返回内容】
- LLM 生成的分析/建议/解释文本"""

    @property
    def parameters(self) -> Dict[str, str]:
        return {
            "task": "推理任务描述",
            "context": "相关上下文信息 (可选, 如之前的查询结果)"
        }

    def execute(self, **kwargs) -> str:
        task = kwargs.get("task", "")
        context = kwargs.get("context", "")

        logger.info(f"llm_reason 执行: task={task[:100]}")

        # TODO: 实现 LLM 推理
        # 1. 构建 System Prompt (职业规划专家角色)
        # 2. 注入 context (画像数据/匹配结果等)
        # 3. LLMClient.chat() 生成回答
        # 4. 返回推理结果

        return f"[llm_reason] 任务: {task}\n暂未实现, 待接入 LLMClient"
