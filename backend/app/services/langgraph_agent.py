"""
CPA-Agent LangGraph 编排引擎
使用 StateGraph + ToolNode + tools_condition

- 结构化 function_calling 替代正则解析
- stream_mode="messages" 原生流式输出
- PyMySQLSaver checkpoint 持久化
- ToolNode 并行执行
- 错误处理 handle_tool_errors
"""

import json
import time
from typing import Annotated, Any, Dict, List, Optional, TypedDict

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import tool as lc_tool
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

from config.prompt_loader import load_prompt

from ..utils.logger import get_logger
from ..utils.llm_client import LLMClient
from ..config import Config

logger = get_logger('cpa-agent.langgraph')


# ═══════════════════════════════════════════════════════════════
# 1. State Schema
# ═══════════════════════════════════════════════════════════════

class CPAState(TypedDict):
    """LangGraph 状态 — 消息列表 + 学生上下文"""
    messages: Annotated[List[BaseMessage], add_messages]
    student_context: Dict[str, Any]


# ═══════════════════════════════════════════════════════════════
# 2. Tool 适配层: BaseTool → langchain @tool
# ═══════════════════════════════════════════════════════════════

# 全局缓存, 避免重复创建
_tool_registry: Dict[str, Any] = {}


def _adapt_existing_tool(base_tool_instance) -> Any:
    """
    将现有 BaseTool 子类实例适配为 langchain @tool 函数。
    利用闭包捕获原始实例的 execute() 方法。
    """
    name = base_tool_instance.name
    if name in _tool_registry:
        return _tool_registry[name]

    # 从 BaseTool.parameters 构造参数签名
    params_schema = base_tool_instance.parameters
    desc = base_tool_instance.description

    # 动态创建 @tool 函数
    @lc_tool(name=name, description=desc)
    def adapted_tool(**kwargs) -> str:
        result = base_tool_instance.execute(**kwargs)
        if isinstance(result, dict):
            return json.dumps(result, ensure_ascii=False)
        return str(result)

    # 注入参数 schema (如果 BaseTool 提供了 JSON Schema 格式)
    if isinstance(params_schema, dict) and "properties" in params_schema:
        adapted_tool.args_schema = type(
            f"{name}Args",
            (),
            {"__annotations__": {
                k: str for k in params_schema.get("properties", {}).keys()
            }},
        )

    _tool_registry[name] = adapted_tool
    return adapted_tool


def _build_langchain_tools() -> List[Any]:
    """从现有 BaseTool 实例构建 langchain tool 列表"""
    from .tools.graph_query import GraphQueryTool
    from .tools.db_query import DbQueryTool
    from .tools.semantic_search import SemanticSearchTool
    from .tools.pipeline_run import PipelineRunTool
    from .tools.llm_reason import LlmReasonTool
    from .tools.skill_tools import (
        SkillExtractTool,
        SkillMatchTool,
        PortraitGenerateTool,
        ReportGenerateTool,
        SkillRecommendTool,
        LearningPathTool,
    )

    # 检查是否有 jd_cleaner
    try:
        from .tools.jd_cleaner import JdCleanerTool
        jd_cleaner = JdCleanerTool()
    except ImportError:
        jd_cleaner = None

    instances = [
        GraphQueryTool(),
        DbQueryTool(),
        SemanticSearchTool(),
        PipelineRunTool(),
        LlmReasonTool(),
        SkillExtractTool(),
        SkillMatchTool(),
        PortraitGenerateTool(),
        ReportGenerateTool(),
        SkillRecommendTool(),
        LearningPathTool(),
    ]
    if jd_cleaner:
        instances.append(jd_cleaner)

    # ── 归一化工具 (原生 @tool, 非 BaseTool) ──
    @lc_tool(name="normalize_entity", description="将实体名归一化为数据库规范名。输入: name(实体名), entity_type(skill/position/major/certificate)。返回: canonical_name, match_type, is_oov。OOV时自动注册到数据库。")
    def normalize_entity_tool(name: str, entity_type: str = "skill", context: str = "") -> str:
        try:
            from .entity_normalizer import get_entity_normalizer
            normalizer = get_entity_normalizer()
            result = normalizer.normalize(name, entity_type, context or None)
            return json.dumps(result.to_dict(), ensure_ascii=False)
        except Exception as e:
            return f"归一化失败: {e}"

    adapted = [_adapt_existing_tool(inst) for inst in instances]
    adapted.append(normalize_entity_tool)
    return adapted


# ═══════════════════════════════════════════════════════════════
# 3. System Prompt — 从 config/prompts/agent.langgraph.system 加载
# ═══════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════
# 4. Agent Node
# ═══════════════════════════════════════════════════════════════

def agent_node(state: CPAState, llm_with_tools) -> Dict:
    """LLM 决策节点 — 调用 LLM 生成回复或工具调用"""
    messages = state["messages"]
    student_context = state.get("student_context", {})

    # 构建系统消息
    system_content = load_prompt("agent.langgraph.system")
    if student_context:
        ctx_str = json.dumps(student_context, ensure_ascii=False, indent=2)
        system_content += f"\n\n【当前学生上下文】\n{ctx_str}"

    # 确保首条消息是 SystemMessage
    if not messages or not isinstance(messages[0], SystemMessage):
        messages = [SystemMessage(content=system_content)] + list(messages)
    else:
        # 替换系统消息
        messages = [SystemMessage(content=system_content)] + list(messages[1:])

    response = llm_with_tools.invoke(messages)
    return {"messages": [response]}


# ═══════════════════════════════════════════════════════════════
# 5. Graph Builder
# ═══════════════════════════════════════════════════════════════

def build_cpa_agent(
    checkpointer=None,
    temperature: float = 0.5,
    max_tokens: int = 4096,
) -> StateGraph:
    """
    构建 CPA-Agent LangGraph 工作流

    Args:
        checkpointer: PyMySQLSaver / MemorySaver 实例
        temperature: LLM 温度
        max_tokens: 最大 token 数

    Returns:
        编译后的 StateGraph
    """
    # 构建 tools
    lc_tools = _build_langchain_tools()

    # 构建 LLM (OpenAI SDK 格式, 兼容 DeepSeek / Qwen)
    llm = ChatOpenAI(
        api_key=Config.LLM_API_KEY,
        base_url=Config.LLM_BASE_URL,
        model=Config.LLM_MODEL_NAME,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=Config.LLM_TIMEOUT,
    )

    # 绑定 tools — LLM 将使用原生 function_calling
    llm_with_tools = llm.bind_tools(lc_tools)

    # ToolNode — 支持并行执行 + 错误处理
    tool_node = ToolNode(
        lc_tools,
        handle_tool_errors=True,
    )

    # ── 构建图 ──
    workflow = StateGraph(CPAState)

    # 节点
    def _agent_node_fn(state: CPAState) -> Dict:
        return agent_node(state, llm_with_tools)

    workflow.add_node("agent", _agent_node_fn)
    workflow.add_node("tools", tool_node)

    # 边
    workflow.set_entry_point("agent")

    # 条件路由: 有 tool_calls → tools; 无 → END
    workflow.add_conditional_edges(
        "agent",
        tools_condition,
        {"tools": "tools", END: END},
    )

    # tools 执行完 → 回到 agent
    workflow.add_edge("tools", "agent")

    # 编译
    compile_kwargs = {}
    if checkpointer:
        compile_kwargs["checkpointer"] = checkpointer

    compiled = workflow.compile(**compile_kwargs)

    logger.info(
        f"CPA-Agent LangGraph 编译完成: "
        f"{len(lc_tools)} tools, "
        f"checkpointer={type(checkpointer).__name__ if checkpointer else 'None'}"
    )

    return compiled


# ═══════════════════════════════════════════════════════════════
# 6. Checkpointer Factory
# ═══════════════════════════════════════════════════════════════

_checkpointer_instance = None


def get_checkpointer():
    """
    获取 MySQL checkpointer (单例)
    使用 PyMySQLSaver 持久化对话状态到 MySQL
    """
    global _checkpointer_instance
    if _checkpointer_instance is not None:
        return _checkpointer_instance

    try:
        from langgraph.checkpoint.mysql.pymysql import PyMySQLSaver

        db_uri = (
            f"mysql://{Config.MYSQL_USER}:{Config.MYSQL_PASSWORD}"
            f"@{Config.MYSQL_HOST}:{Config.MYSQL_PORT}/{Config.MYSQL_DATABASE}"
        )

        _checkpointer_instance = PyMySQLSaver.from_conn_string(db_uri)
        # 首次使用需要 setup() 创建 checkpoint 表
        _checkpointer_instance.setup()
        logger.info("MySQL Checkpointer 初始化成功")
        return _checkpointer_instance

    except Exception as e:
        logger.warning(f"MySQL Checkpointer 初始化失败, 回退 MemorySaver: {e}")
        from langgraph.checkpoint.memory import MemorySaver
        _checkpointer_instance = MemorySaver()
        return _checkpointer_instance


# ═══════════════════════════════════════════════════════════════
# 7. High-Level API — 供 Flask 路由调用
# ═══════════════════════════════════════════════════════════════

class LangGraphAgentService:
    """
    LangGraph Agent 服务层
    封装编译、调用、流式输出, 供 Flask API 调用
    """

    def __init__(self, use_checkpointer: bool = True):
        checkpointer = get_checkpointer() if use_checkpointer else None
        self.graph = build_cpa_agent(checkpointer=checkpointer)
        self._use_checkpointer = use_checkpointer

    def _select_subagent(self, message: str, student_context: Optional[Dict]) -> str:
        """显式子Agent路由: planner / profile / match / report / qa"""
        text = (message or "").lower()
        ctx = student_context or {}
        business = ctx.get("business", {}) if isinstance(ctx, dict) else {}

        if any(k in text for k in ["报告", "report", "职业报告"]):
            return "report_subagent"
        if any(k in text for k in ["匹配", "match", "岗位", "投递"]):
            return "match_subagent"
        if any(k in text for k in ["画像", "portrait", "能力", "维度"]):
            return "profile_subagent"
        if any(k in text for k in ["规划", "学习路径", "计划", "roadmap"]):
            return "planner_subagent"

        # 基于结构化上下文的默认路由
        if business.get("match_result"):
            return "planner_subagent"
        if business.get("portrait"):
            return "match_subagent"
        return "qa_subagent"

    def _with_orchestration_context(self, message: str, student_context: Optional[Dict]) -> Dict[str, Any]:
        base = dict(student_context or {})
        active_subagent = self._select_subagent(message, base)
        orchestration = base.get("orchestration", {}) if isinstance(base.get("orchestration"), dict) else {}
        orchestration.update({
            "active_subagent": active_subagent,
            "strategy": "explicit_router",
        })
        base["orchestration"] = orchestration
        return base

    def chat(
        self,
        message: str,
        session_id: Optional[str] = None,
        student_context: Optional[Dict] = None,
        history: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        """
        同步对话 — 返回完整回复

        Args:
            message: 用户消息
            session_id: 会话 ID (用于 checkpoint 恢复)
            student_context: 学生上下文
            history: 历史对话 [{"role": "user/assistant", "content": "..."}]

        Returns:
            {reply, tool_calls, session_id, elapsed_seconds}
        """
        start_time = time.time()

        routed_context = self._with_orchestration_context(message, student_context)
        active_subagent = routed_context.get("orchestration", {}).get("active_subagent", "qa_subagent")

        # 构建初始消息

        messages: List[BaseMessage] = []
        if history:
            for h in history:
                role = h.get("role", "user")
                content = h.get("content", "")
                if role == "user":
                    messages.append(HumanMessage(content=content))
                elif role == "assistant":
                    messages.append(AIMessage(content=content))

        messages.append(HumanMessage(content=message))

        # Config (thread_id 用于 checkpoint)
        config = {}
        if session_id and self._use_checkpointer:
            config["configurable"] = {"thread_id": session_id}

        # 调用 graph
        result = self.graph.invoke(
            {"messages": messages, "student_context": routed_context},
            config=config or None,
        )

        # 提取最终回复
        final_message = result["messages"][-1] if result["messages"] else None
        reply = final_message.content if final_message else "抱歉, 无法生成回复。"

        # 提取 tool_calls 记录
        tool_call_records = []
        for msg in result["messages"]:
            if isinstance(msg, AIMessage) and msg.tool_calls:
                for tc in msg.tool_calls:
                    tool_call_records.append({
                        "tool_name": tc["name"],
                        "parameters": tc["args"],
                    })
            if isinstance(msg, ToolMessage):
                # 找到对应的 tool_call
                tool_call_records_with_result = [
                    r for r in tool_call_records
                    if r.get("tool_name") == msg.name
                ]
                if tool_call_records_with_result:
                    tool_call_records_with_result[-1]["result_preview"] = (
                        msg.content[:200] if isinstance(msg.content, str) else str(msg.content)[:200]
                    )

        elapsed = time.time() - start_time

        return {
            "reply": reply,
            "tool_calls": tool_call_records,
            "session_id": session_id,
            "elapsed_seconds": round(elapsed, 2),
            "active_subagent": active_subagent,
        }

    def chat_stream(
        self,
        message: str,
        session_id: Optional[str] = None,
        student_context: Optional[Dict] = None,
        history: Optional[List[Dict[str, str]]] = None,
    ):
        """
        SSE 流式对话 — yield 逐 token 输出

        Yields:
            SSE 格式字符串: "data: {json}\n\n"
        """
        routed_context = self._with_orchestration_context(message, student_context)
        active_subagent = routed_context.get("orchestration", {}).get("active_subagent", "qa_subagent")

        # 构建初始消息
        messages: List[BaseMessage] = []
        if history:
            for h in history:
                role = h.get("role", "user")
                content = h.get("content", "")
                if role == "user":
                    messages.append(HumanMessage(content=content))
                elif role == "assistant":
                    messages.append(AIMessage(content=content))

        messages.append(HumanMessage(content=message))

        # Config
        config = {}
        if session_id and self._use_checkpointer:
            config["configurable"] = {"thread_id": session_id}

        # 先输出路由事件
        route_data = json.dumps({
            "type": "orchestration",
            "active_subagent": active_subagent,
            "strategy": "explicit_router",
        }, ensure_ascii=False)
        yield f"data: {route_data}\n\n"

        # 流式调用
        for event in self.graph.stream(
            {"messages": messages, "student_context": routed_context},
            config=config or None,
            stream_mode=["messages"],
        ):
            # event = (message_chunk, metadata)
            if isinstance(event, tuple) and len(event) == 2:
                chunk, metadata = event
            else:
                chunk = event
                metadata = {}

            # 只输出内容 chunk
            if hasattr(chunk, 'content') and chunk.content:
                sse_data = json.dumps({
                    "type": "token",
                    "content": chunk.content,
                    "node": metadata.get("langgraph_node", ""),
                }, ensure_ascii=False)
                yield f"data: {sse_data}\n\n"

            # tool_call 事件
            if hasattr(chunk, 'tool_calls') and chunk.tool_calls:
                for tc in chunk.tool_calls:
                    sse_data = json.dumps({
                        "type": "tool_call",
                        "tool_name": tc["name"],
                        "parameters": tc["args"],
                    }, ensure_ascii=False)
                    yield f"data: {sse_data}\n\n"

        # 结束标记
        yield "data: [DONE]\n\n"


# ═══════════════════════════════════════════════════════════════
# 8. 单例管理
# ═══════════════════════════════════════════════════════════════

_agent_service = None


def get_langgraph_agent() -> LangGraphAgentService:
    """获取 LangGraph Agent 服务单例"""
    global _agent_service
    if _agent_service is None:
        _agent_service = LangGraphAgentService(use_checkpointer=True)
    return _agent_service
