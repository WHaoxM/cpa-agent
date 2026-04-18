"""
Tool 5: llm_reason — LLM 自然语言推理
职业建议、报告润色、匹配分数解释等需要 LLM 深度思考的场景
"""

from typing import Dict, Optional

from .base import BaseTool
from ...utils.logger import get_logger
from ...utils.llm_client import LLMClient

logger = get_logger('cpa-agent.tool.llm_reason')

SYSTEM_PROMPT = """你是 CPA-Agent 职业规划专家，擅长：
1. 分析学生的技能画像与岗位需求的匹配度
2. 提供职业发展建议和学习路径规划
3. 解释人岗匹配分数的构成和优化方向
4. 撰写专业的职业发展报告

请基于提供的上下文信息，给出专业、实用的建议。"""


class LlmReasonTool(BaseTool):
    """LLM 自然语言推理工具"""

    def __init__(self):
        super().__init__()
        self._llm = None

    @property
    def llm(self) -> LLMClient:
        if self._llm is None:
            self._llm = LLMClient()
        return self._llm

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

        if not task:
            return "[llm_reason] 错误: task 参数不能为空"

        logger.info(f"llm_reason 执行: task={task[:100]}")

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]

        if context:
            user_content = f"上下文信息:\n{context}\n\n---\n\n任务: {task}"
        else:
            user_content = task

        messages.append({"role": "user", "content": user_content})

        try:
            result = self.llm.chat(
                messages=messages,
                temperature=0.7,
                max_tokens=2048
            )
            logger.info(f"llm_reason 完成: 响应长度={len(result)}")
            return result
        except Exception as e:
            logger.error(f"llm_reason 执行失败: {e}")
            return f"[llm_reason] 推理失败: {str(e)}"
