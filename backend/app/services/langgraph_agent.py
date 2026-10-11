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
    convert_to_openai_messages,
)
from langchain_core.runnables import Runnable
from langchain_core.tools import tool as lc_tool
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field, create_model

try:
    from langgraph.prebuilt import ToolNode, tools_condition
except ImportError as _lg_err:  # pragma: no cover
    raise ImportError(
        "缺少 langgraph.prebuilt.ToolNode。请在 mkg 中安装: "
        'pip install "langgraph>=0.4.0" "langgraph-prebuilt>=0.1.0"'
    ) from _lg_err

from config.prompt_loader import load_prompt

from ..utils.logger import get_logger
from ..utils.llm_client import LLMClient
from .agent_runtime.budget import (
    context_window_status, record_model_call, require_turn_token_capacity,
)
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

_JSON_TYPE_MAP = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def _parameters_to_args_model(tool_name: str, params_schema: Any) -> Optional[type[BaseModel]]:
    """BaseTool.parameters（JSON Schema 或 {名:说明}）→ Pydantic args_schema。"""
    if not isinstance(params_schema, dict) or not params_schema:
        return None

    fields: Dict[str, Any] = {}
    if "properties" in params_schema:
        required = set(params_schema.get("required") or [])
        for key, prop in (params_schema.get("properties") or {}).items():
            prop = prop if isinstance(prop, dict) else {}
            py_type = _JSON_TYPE_MAP.get(str(prop.get("type") or "string"), str)
            desc = str(prop.get("description") or "")
            if key in required:
                fields[key] = (py_type, Field(description=desc))
            else:
                default = prop.get("default", None)
                fields[key] = (Optional[py_type], Field(default=default, description=desc))
    else:
        for key, desc in params_schema.items():
            if key in ("type", "required", "properties"):
                continue
            fields[str(key)] = (str, Field(default="", description=str(desc)))

    if not fields:
        return None
    return create_model(f"{tool_name}Args", **fields)


def _adapt_existing_tool(base_tool_instance) -> Any:
    """
    将现有 BaseTool 子类实例适配为 langchain @tool 函数。
    利用闭包捕获原始实例的 execute() 方法。
    """
    name = base_tool_instance.name
    if name in _tool_registry:
        return _tool_registry[name]

    desc = base_tool_instance.description
    args_model = _parameters_to_args_model(name, base_tool_instance.parameters)

    def adapted_tool(**kwargs) -> str:
        result = base_tool_instance.execute(**kwargs)
        if isinstance(result, dict):
            return json.dumps(result, ensure_ascii=False)
        return str(result)

    adapted_tool.__name__ = name
    adapted_tool.__doc__ = desc

    # langchain_core>=1.x: 工具名走首位位置参数，不再接受 name=
    if args_model is not None:
        wrapped = lc_tool(name, description=desc, args_schema=args_model)(adapted_tool)
    else:
        wrapped = lc_tool(name, description=desc)(adapted_tool)

    _tool_registry[name] = wrapped
    return wrapped


def _build_langchain_tools() -> List[Any]:
    """从现有 BaseTool 实例构建 langchain tool 列表"""
    from .tools.graph_query import GraphQueryTool
    from .tools.db_query import DbQueryTool
    from .tools.career_data_read import CareerDataReadTool
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
        CareerDataReadTool(),
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
    @lc_tool(
        "normalize_entity",
        description="将实体名归一化为数据库规范名。输入: name(实体名), entity_type(skill/position/major/certificate)。返回: canonical_name, match_type, is_oov。OOV时自动注册到数据库。",
    )
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

def _build_llm_with_tools(lc_tools: List[Any], temperature: float, max_tokens: int):
    """按 LLM_FAILOVER_ORDER 构建 ChatOpenAI + tools；402/鉴权等自动落到下一厂商。"""
    from config.integration_loader import get_failover_order, get_provider_config
    from langchain_core.utils.function_calling import convert_to_openai_tool

    class BudgetedCandidates(Runnable):
        """Guard the *final* LangChain messages on every tool round and fallback."""

        def __init__(self, candidates, tool_schema):
            self.candidates = candidates
            self.tool_schema = tool_schema

        def _checked(self, messages):
            import json

            # LangChain's own serializer retains tool_call arguments and
            # multimodal blocks; unsupported content fails closed below.
            wire = convert_to_openai_messages(messages)
            if isinstance(wire, dict):
                wire = [wire]
            if self.tool_schema:
                wire = [{"role": "system", "content": json.dumps(
                    self.tool_schema, ensure_ascii=False, separators=(",", ":"),
                )}] + wire
            return wire

        @staticmethod
        def _usage(response):
            metadata = getattr(response, "usage_metadata", None) or {}
            if metadata:
                return {
                    "prompt_tokens": metadata.get("input_tokens"),
                    "completion_tokens": metadata.get("output_tokens"),
                    "total_tokens": metadata.get("total_tokens"),
                }
            raw = (getattr(response, "response_metadata", None) or {}).get("token_usage") or {}
            if raw:
                return {key: raw.get(key) for key in (
                    "prompt_tokens", "completion_tokens", "total_tokens",
                )}
            return None

        def invoke(self, input, config=None, **kwargs):
            wire = self._checked(input)
            budget_errors = []
            provider_errors = []
            for provider, model, bound in self.candidates:
                status = context_window_status(provider, model, wire, max_tokens)
                if status.fits is not True:
                    budget_errors.append(f"{provider}/{model}: {status.reason}")
                    continue
                require_turn_token_capacity(max_tokens, estimated_input_tokens=status.estimated_input_tokens or 0)
                try:
                    response = bound.invoke(input, config=config, **kwargs)
                except Exception as exc:
                    record_model_call(
                        provider=provider, model=model, stage="langgraph",
                        usage=None, preflight=status, finish_reason="provider_error",
                    )
                    require_turn_token_capacity(0)
                    provider_errors.append(f"{provider}/{model}: {exc}")
                    continue
                response_metadata = getattr(response, "response_metadata", None) or {}
                finish_reason = response_metadata.get("finish_reason")
                actual_model = response_metadata.get("model_name") or model
                record_model_call(
                    provider=provider, model=actual_model, stage="langgraph",
                    usage=self._usage(response), preflight=status,
                    finish_reason=finish_reason,
                )
                require_turn_token_capacity(0)
                if actual_model != model:
                    raise RuntimeError(
                        f"provider_model_mismatch: requested={model} actual={actual_model}"
                    )
                if finish_reason == "length":
                    raise RuntimeError(f"LangGraph output truncated provider={provider} model={model}")
                return response
            raise RuntimeError(
                "LangGraph providers failed or context budget unproven: "
                + " | ".join(budget_errors + provider_errors)
            )

        def stream(self, input, config=None, **kwargs):
            # The main LangGraph path invokes the model in agent_node. Keep
            # direct streaming guarded as well for future callers.
            wire = self._checked(input)
            errors = []
            for provider, model, bound in self.candidates:
                status = context_window_status(provider, model, wire, max_tokens)
                if status.fits is not True:
                    errors.append(f"{provider}/{model}: {status.reason}")
                    continue
                require_turn_token_capacity(max_tokens, estimated_input_tokens=status.estimated_input_tokens or 0)
                emitted = False
                last_chunk = None
                observed_usage = None
                try:
                    for chunk in bound.stream(input, config=config, **kwargs):
                        emitted = True
                        last_chunk = chunk
                        observed_usage = self._usage(chunk) or observed_usage
                        yield chunk
                except Exception:
                    record_model_call(
                        provider=provider, model=model, stage="langgraph.stream",
                        usage=observed_usage, preflight=status,
                        finish_reason="stream_error",
                    )
                    require_turn_token_capacity(0)
                    raise
                metadata = getattr(last_chunk, "response_metadata", None) or {}
                finish_reason = metadata.get("finish_reason")
                actual_model = metadata.get("model_name") or model
                record_model_call(
                    provider=provider, model=actual_model, stage="langgraph.stream",
                    usage=observed_usage, preflight=status,
                    finish_reason=finish_reason,
                )
                require_turn_token_capacity(0)
                if actual_model != model:
                    raise RuntimeError(
                        f"provider_model_mismatch: requested={model} actual={actual_model}"
                    )
                if finish_reason == "length":
                    raise RuntimeError(f"LangGraph output truncated provider={provider} model={model}")
                return
            raise RuntimeError("LangGraph streaming context budget unproven: " + " | ".join(errors))

    bound: List[Any] = []
    for provider in get_failover_order():
        cfg = get_provider_config(provider)
        if not cfg.get("api_key") or not cfg.get("base_url") or not cfg.get("model"):
            continue
        llm = ChatOpenAI(
            api_key=cfg["api_key"],
            base_url=cfg["base_url"],
            model=cfg["model"],
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=cfg.get("timeout") or Config.LLM_TIMEOUT,
            max_retries=0,
        )
        bound.append((provider, cfg["model"], llm.bind_tools(lc_tools)))
        logger.info(
            "LangGraph LLM candidate provider=%s model=%s",
            provider,
            cfg["model"],
        )

    if not bound:
        raise ValueError("未配置可用 LLM（LangGraph 需要至少一个 provider 的 API Key）")
    return BudgetedCandidates(bound, [convert_to_openai_tool(tool) for tool in lc_tools])


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

    # 绑定 tools + 多厂商 failover（DeepSeek 402 → Qwen）
    llm_with_tools = _build_llm_with_tools(lc_tools, temperature, max_tokens)

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


_checkpointer_cm = None  # 保持 from_conn_string 的 context manager 存活


def _mysql_checkpoint_uri() -> str:
    """构造 PyMySQLSaver 连接串，与业务 MySQL 使用同一套 Config。

    注意：langgraph/pymysql 对 userinfo 里的 %XX 解码不完整。
    把密码里的 ``!`` 编成 ``%21`` 会导致 1045 Access denied。
    因此只编码会破坏 URI 结构的字符（:@/?# 等），保留 ``!`` 等子分隔符原文。
    """
    from urllib.parse import quote

    user = quote(str(Config.MYSQL_USER or ""), safe="")
    # safe 保留 ! 等；必须编码 @ : / ? # % 以免截断 userinfo/host
    password = quote(str(Config.MYSQL_PASSWORD or ""), safe="!")
    return (
        f"mysql://{user}:{password}"
        f"@{Config.MYSQL_HOST}:{Config.MYSQL_PORT}/{Config.MYSQL_DATABASE}"
    )


def _align_checkpoint_collation() -> None:
    """对齐 checkpoint 表 collation，消除 1267 Illegal mix of collations。

    库默认多为 utf8mb4_unicode_ci；langgraph-checkpoint-mysql 的 JSON_TABLE
    只写了 CHARACTER SET utf8mb4、未写 COLLATE，MySQL 8 会落到
    utf8mb4_0900_ai_ci，与表列比较时报错。
    见 https://github.com/tjni/langgraph-checkpoint-mysql/issues/55
    """
    from .agent_runtime.checkpoint_mysql import align_checkpoint_collation
    align_checkpoint_collation()


def get_checkpointer():
    """
    获取 MySQL checkpointer (单例)
    使用 PyMySQLSaver 持久化对话状态到 MySQL
    """
    global _checkpointer_instance, _checkpointer_cm
    if _checkpointer_instance is not None:
        return _checkpointer_instance

    try:
        from langgraph.checkpoint.mysql.pymysql import PyMySQLSaver

        db_uri = _mysql_checkpoint_uri()

        # 新版 from_conn_string → GeneratorContextManager，须 enter 且勿提前 exit
        _checkpointer_cm = PyMySQLSaver.from_conn_string(db_uri)
        saver = _checkpointer_cm.__enter__()
        saver.setup()
        try:
            _align_checkpoint_collation()
        except Exception as align_err:
            logger.warning("checkpoint collation 对齐失败（可稍后跑 fix SQL）: %s", align_err)
        _checkpointer_instance = saver
        logger.info("MySQL Checkpointer 初始化成功")
        return _checkpointer_instance

    except Exception as e:
        logger.warning(
            "MySQL Checkpointer 初始化失败, 回退 MemorySaver: %s: %s",
            type(e).__name__,
            e,
        )
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
