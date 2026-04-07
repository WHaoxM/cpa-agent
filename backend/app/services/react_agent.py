"""
ReAct Agent — CPA-Agent 智能体核心
参考 MiroFish ReportAgent 的 ReAct 循环实现, 适配职业规划场景

架构:
  用户输入 → Redis 取 session → LLM 意图识别 → ReAct 推理链
  → Tool 调用 (graph_query / db_query / semantic_search / pipeline_run / llm_reason)
  → 结果组装 → 自然语言回复 + 存储

满足 R-11: "采用大模型为大脑并自主执行相应任务就是智能体"
"""

import json
import re
import time
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass, field
from datetime import datetime

from ..config import Config
from ..utils.llm_client import LLMClient
from ..utils.logger import get_logger
from .tools.base import BaseTool
from .tools.graph_query import GraphQueryTool
from .tools.db_query import DbQueryTool
from .tools.semantic_search import SemanticSearchTool
from .tools.pipeline_run import PipelineRunTool
from .tools.llm_reason import LlmReasonTool

logger = get_logger('cpa-agent.react_agent')


# ═══════════════════════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════════════════════

@dataclass
class ToolCallRecord:
    """单次工具调用记录"""
    tool_name: str
    parameters: Dict[str, Any]
    result: str
    elapsed_seconds: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "parameters": self.parameters,
            "result_preview": self.result[:200] + "..." if len(self.result) > 200 else self.result,
            "elapsed_seconds": round(self.elapsed_seconds, 2)
        }


@dataclass
class AgentResponse:
    """智能体回复"""
    reply: str
    tool_calls: List[ToolCallRecord] = field(default_factory=list)
    thinking_steps: List[str] = field(default_factory=list)
    total_iterations: int = 0
    elapsed_seconds: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "reply": self.reply,
            "tool_calls": [tc.to_dict() for tc in self.tool_calls],
            "thinking_steps": self.thinking_steps,
            "total_iterations": self.total_iterations,
            "elapsed_seconds": round(self.elapsed_seconds, 2)
        }


# ═══════════════════════════════════════════════════════════════
# Prompt 模板
# ═══════════════════════════════════════════════════════════════

AGENT_SYSTEM_PROMPT = """\
你是 CPA-Agent, 一个专业的大学生职业规划智能体。你能够：
1. 查询知识图谱中的技能、岗位、职业路径信息
2. 查询学生画像、岗位画像、匹配结果等业务数据
3. 进行语义检索, 找到相似技能、岗位或历史对话
4. 触发数据处理管线 (简历解析、画像生成、匹配分析、报告生成)
5. 进行深度推理, 给出职业建议和分析

═══════════════════════════════════════════════════════════════
【可用工具】
═══════════════════════════════════════════════════════════════

{tools_description}

═══════════════════════════════════════════════════════════════
【工作流程 — 每次回复只能做以下两件事之一】
═══════════════════════════════════════════════════════════════

选项A — 调用工具:
输出你的思考, 然后用以下格式调用一个工具:
<tool_call>
{{"name": "工具名称", "parameters": {{"参数名": "参数值"}}}}
</tool_call>
系统会执行工具并把结果返回给你。

选项B — 输出最终回复:
当你已获取足够信息, 以 "Final Answer:" 开头输出最终回复。

⚠️ 严格禁止:
- 禁止在一次回复中同时包含工具调用和 Final Answer
- 禁止自己编造工具返回结果
- 每次回复最多调用一个工具

═══════════════════════════════════════════════════════════════
【回复风格】
═══════════════════════════════════════════════════════════════

- 专业、友好、简洁
- 用数据说话, 引用具体的匹配分数、技能覆盖率等
- 给出可操作的建议, 而非泛泛而谈
- 使用 Markdown 格式组织回复"""

OBSERVATION_TEMPLATE = """\
Observation (工具返回结果):

═══ 工具 {tool_name} 返回 ═══
{result}

═══════════════════════════════════════════════════════════════
已调用工具 {tool_calls_count}/{max_tool_calls} 次
- 如果信息充分: 以 "Final Answer:" 开头输出最终回复
- 如果需要更多信息: 继续调用工具
═══════════════════════════════════════════════════════════════"""

FORCE_FINAL_MSG = "已达到工具调用上限, 请直接以 \"Final Answer:\" 开头输出最终回复。"

CONFLICT_MSG = """\
【格式错误】你在一次回复中同时包含了工具调用和 Final Answer, 这是不允许的。
每次回复只能做以下两件事之一:
- 调用一个工具 (输出一个 <tool_call> 块, 不要写 Final Answer)
- 输出最终回复 (以 'Final Answer:' 开头, 不要包含 <tool_call>)
请重新回复, 只做其中一件事。"""


# ═══════════════════════════════════════════════════════════════
# ReactAgent 主类
# ═══════════════════════════════════════════════════════════════

class ReactAgent:
    """
    ReAct Agent — 职业规划智能体核心

    ReAct 循环:
    1. Thought (思考) — 分析用户意图, 决定需要什么信息
    2. Action (行动) — 调用工具获取信息
    3. Observation (观察) — 分析工具返回结果
    4. 重复直到信息足够或达到最大次数
    5. Final Answer (最终回答) — 生成回复
    """

    # 合法工具名称集合, 用于裸 JSON 兜底解析时校验
    VALID_TOOL_NAMES = {"graph_query", "db_query", "semantic_search", "pipeline_run", "llm_reason"}

    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
        max_tool_calls: Optional[int] = None,
        max_iterations: Optional[int] = None,
        temperature: Optional[float] = None,
    ):
        self.llm = llm_client or LLMClient()
        self.max_tool_calls = max_tool_calls or Config.AGENT_MAX_TOOL_CALLS
        self.max_iterations = max_iterations or Config.AGENT_MAX_ITERATIONS
        self.temperature = temperature or Config.AGENT_TEMPERATURE

        # 初始化 5 个 Tool
        self.tools: Dict[str, BaseTool] = {}
        self._register_tools()

        logger.info(
            f"ReactAgent 初始化: max_tool_calls={self.max_tool_calls}, "
            f"max_iterations={self.max_iterations}"
        )

    def _register_tools(self):
        """注册所有工具"""
        tool_instances = [
            GraphQueryTool(),
            DbQueryTool(),
            SemanticSearchTool(),
            PipelineRunTool(),
            LlmReasonTool(),
        ]
        for tool in tool_instances:
            self.tools[tool.name] = tool

    def _get_tools_description(self) -> str:
        """生成工具描述文本 (注入 System Prompt)"""
        parts = []
        for name, tool in self.tools.items():
            params_desc = ", ".join(f"{k}: {v}" for k, v in tool.parameters.items())
            parts.append(f"- {name}: {tool.description}")
            if params_desc:
                parts.append(f"  参数: {params_desc}")
        return "\n".join(parts)

    def _execute_tool(self, tool_name: str, parameters: Dict[str, Any]) -> str:
        """
        执行工具调用

        Args:
            tool_name: 工具名称
            parameters: 工具参数

        Returns:
            文本格式的结果
        """
        if tool_name not in self.tools:
            return f"未知工具: {tool_name}。可用工具: {', '.join(self.tools.keys())}"

        try:
            return self.tools[tool_name].execute(**parameters)
        except Exception as e:
            logger.error(f"工具 {tool_name} 执行失败: {str(e)}")
            return f"工具执行失败: {str(e)}"

    def _parse_tool_calls(self, response: str) -> List[Dict[str, Any]]:
        """
        从 LLM 响应中解析工具调用
        参考 MiroFish: 支持 <tool_call> XML 标签 + 裸 JSON 兜底

        Returns:
            工具调用列表 [{"name": "...", "parameters": {...}}]
        """
        tool_calls = []

        # 格式1: <tool_call>{"name": "...", "parameters": {...}}</tool_call>
        xml_pattern = r'<tool_call>\s*(\{.*?\})\s*</tool_call>'
        for match in re.finditer(xml_pattern, response, re.DOTALL):
            try:
                call_data = json.loads(match.group(1))
                if self._is_valid_tool_call(call_data):
                    tool_calls.append(call_data)
            except json.JSONDecodeError:
                pass

        if tool_calls:
            return tool_calls

        # 格式2: 兜底 — 裸 JSON (LLM 没包 <tool_call> 标签)
        stripped = response.strip()
        if stripped.startswith('{') and stripped.endswith('}'):
            try:
                call_data = json.loads(stripped)
                if self._is_valid_tool_call(call_data):
                    tool_calls.append(call_data)
                    return tool_calls
            except json.JSONDecodeError:
                pass

        # 格式3: 思考文字 + 尾部裸 JSON
        json_pattern = r'(\{"(?:name|tool)"\s*:.*?\})\s*$'
        match = re.search(json_pattern, stripped, re.DOTALL)
        if match:
            try:
                call_data = json.loads(match.group(1))
                if self._is_valid_tool_call(call_data):
                    tool_calls.append(call_data)
            except json.JSONDecodeError:
                pass

        return tool_calls

    def _is_valid_tool_call(self, data: dict) -> bool:
        """校验解析出的 JSON 是否是合法的工具调用"""
        tool_name = data.get("name") or data.get("tool")
        if tool_name and tool_name in self.VALID_TOOL_NAMES:
            if "tool" in data:
                data["name"] = data.pop("tool")
            if "params" in data and "parameters" not in data:
                data["parameters"] = data.pop("params")
            return True
        return False

    def chat(
        self,
        message: str,
        history: Optional[List[Dict[str, str]]] = None,
        student_context: Optional[Dict[str, Any]] = None,
    ) -> AgentResponse:
        """
        智能体对话 — ReAct 循环核心

        Args:
            message: 用户消息
            history: 历史对话 [{"role": "user/assistant", "content": "..."}]
            student_context: 学生上下文 (student_id, 当前画像摘要等)

        Returns:
            AgentResponse: 包含回复、工具调用记录、思考步骤
        """
        start_time = time.time()

        # 构建 System Prompt
        system_prompt = AGENT_SYSTEM_PROMPT.format(
            tools_description=self._get_tools_description()
        )

        # 注入学生上下文
        if student_context:
            ctx_str = json.dumps(student_context, ensure_ascii=False, indent=2)
            system_prompt += f"\n\n【当前学生上下文】\n{ctx_str}"

        # 构建消息列表
        messages = [{"role": "system", "content": system_prompt}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": message})

        # ReAct 循环
        tool_calls_count = 0
        tool_call_records: List[ToolCallRecord] = []
        thinking_steps: List[str] = []
        conflict_retries = 0

        for iteration in range(self.max_iterations):
            # 调用 LLM
            response = self.llm.chat(
                messages=messages,
                temperature=self.temperature,
                max_tokens=4096
            )

            if response is None:
                logger.warning(f"LLM 返回 None, iteration={iteration + 1}")
                if iteration < self.max_iterations - 1:
                    messages.append({"role": "assistant", "content": "(响应为空)"})
                    messages.append({"role": "user", "content": "请继续回答。"})
                    continue
                break

            # 解析
            tool_calls = self._parse_tool_calls(response)
            has_tool_calls = bool(tool_calls)
            has_final_answer = "Final Answer:" in response

            # ── 冲突处理: 同时输出了工具调用和 Final Answer ──
            if has_tool_calls and has_final_answer:
                conflict_retries += 1
                logger.warning(f"冲突: tool_call + Final Answer, 第 {conflict_retries} 次")

                if conflict_retries <= 2:
                    messages.append({"role": "assistant", "content": response})
                    messages.append({"role": "user", "content": CONFLICT_MSG})
                    continue
                else:
                    # 第3次: 降级处理, 截断到第一个工具调用
                    first_end = response.find('</tool_call>')
                    if first_end != -1:
                        response = response[:first_end + len('</tool_call>')]
                        tool_calls = self._parse_tool_calls(response)
                        has_tool_calls = bool(tool_calls)
                    has_final_answer = False
                    conflict_retries = 0

            # ── 情况1: Final Answer ──
            if has_final_answer:
                final_answer = response.split("Final Answer:")[-1].strip()
                logger.info(f"ReAct 完成: {tool_calls_count} 次工具调用, {iteration + 1} 轮迭代")

                return AgentResponse(
                    reply=final_answer,
                    tool_calls=tool_call_records,
                    thinking_steps=thinking_steps,
                    total_iterations=iteration + 1,
                    elapsed_seconds=time.time() - start_time
                )

            # ── 情况2: 工具调用 ──
            if has_tool_calls:
                if tool_calls_count >= self.max_tool_calls:
                    messages.append({"role": "assistant", "content": response})
                    messages.append({"role": "user", "content": FORCE_FINAL_MSG})
                    continue

                call = tool_calls[0]
                tool_name = call["name"]
                parameters = call.get("parameters", {})

                # 记录思考步骤
                thought = response.split('<tool_call>')[0].strip()
                if thought:
                    thinking_steps.append(thought)

                logger.info(f"工具调用: {tool_name}, params={parameters}")

                # 执行工具
                tool_start = time.time()
                result = self._execute_tool(tool_name, parameters)
                tool_elapsed = time.time() - tool_start

                tool_call_records.append(ToolCallRecord(
                    tool_name=tool_name,
                    parameters=parameters,
                    result=result,
                    elapsed_seconds=tool_elapsed
                ))

                tool_calls_count += 1

                # 注入 Observation 继续循环
                messages.append({"role": "assistant", "content": response})
                messages.append({
                    "role": "user",
                    "content": OBSERVATION_TEMPLATE.format(
                        tool_name=tool_name,
                        result=result,
                        tool_calls_count=tool_calls_count,
                        max_tool_calls=self.max_tool_calls,
                    )
                })
                continue

            # ── 情况3: 既没工具调用也没 Final Answer — 当作直接回复 ──
            logger.info(f"LLM 直接回复 (无前缀), iteration={iteration + 1}")
            return AgentResponse(
                reply=response.strip(),
                tool_calls=tool_call_records,
                thinking_steps=thinking_steps,
                total_iterations=iteration + 1,
                elapsed_seconds=time.time() - start_time
            )

        # 达到最大迭代次数, 强制生成
        logger.warning(f"ReAct 达到最大迭代 {self.max_iterations}, 强制收尾")
        messages.append({"role": "user", "content": FORCE_FINAL_MSG})

        response = self.llm.chat(
            messages=messages,
            temperature=self.temperature,
            max_tokens=4096
        )

        if response and "Final Answer:" in response:
            final_answer = response.split("Final Answer:")[-1].strip()
        elif response:
            final_answer = response.strip()
        else:
            final_answer = "抱歉, 当前无法生成回复, 请稍后重试。"

        return AgentResponse(
            reply=final_answer,
            tool_calls=tool_call_records,
            thinking_steps=thinking_steps,
            total_iterations=self.max_iterations,
            elapsed_seconds=time.time() - start_time
        )
