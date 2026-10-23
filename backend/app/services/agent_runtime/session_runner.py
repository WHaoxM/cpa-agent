"""Session Runner - 参考OpenCode架构

核心设计：
1. 双层while循环：外层处理steer/queue输入，内层执行provider turn
2. needsContinuation控制逻辑
3. 步骤限制和错误恢复
4. 工具执行等待
5. 自动重试机制
"""

from __future__ import annotations

import json
import time
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple
import logging
import threading

logger = logging.getLogger(__name__)


class RunnerState(Enum):
    """Runner状态"""
    IDLE = "idle"
    RUNNING = "running"
    WAITING_INPUT = "waiting_input"
    WAITING_TOOL = "waiting_tool"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class TurnResult(Enum):
    """Turn结果"""
    CONTINUE = "continue"
    SETTLE = "settle"
    FAIL = "fail"
    INTERRUPT = "interrupt"


@dataclass
class TurnContext:
    """Turn上下文"""
    step: int
    messages: List[Dict[str, Any]]
    tool_definitions: List[Dict[str, Any]]
    needs_continuation: bool = False
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None
    tokens_used: int = 0


@dataclass
class RunnerResult:
    """Runner结果"""
    success: bool
    output: Any = None
    steps: int = 0
    errors: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


class SessionRunner:
    """
    Session Runner - 参考OpenCode SessionRunner
    
    核心：双层while循环
    - 外层：处理steer/queue输入
    - 内层：执行provider turn直到settled
    """
    
    def __init__(
        self,
        llm_client: Any = None,
        tool_registry: Any = None,
        session_input: Any = None,
        compaction: Any = None,
        max_steps: int = 10,
        max_retries: int = 3,
    ):
        self.llm_client = llm_client
        self.tool_registry = tool_registry
        self.session_input = session_input
        self.compaction = compaction
        self.max_steps = max_steps
        self.max_retries = max_retries
        self._lock = threading.Lock()
    
    def run(
        self,
        session_id: str,
        initial_messages: List[Dict[str, Any]],
        system_prompt: str = "",
        agent_config: Optional[Dict[str, Any]] = None,
        **kwargs,
    ) -> RunnerResult:
        """
        执行Agent Loop（双层while循环）
        
        Args:
            session_id: 会话ID
            initial_messages: 初始消息列表
            system_prompt: 系统提示
            agent_config: Agent配置
        
        Returns:
            RunnerResult
        """
        logger.info(f"Starting session runner for session: {session_id}")
        
        # 初始化上下文
        messages = list(initial_messages)
        context_limit = kwargs.get("context_limit", 12000)
        
        # 获取Agent配置
        config = agent_config or {}
        max_steps = config.get("max_steps", self.max_steps)
        
        # ── 外层while循环：处理steer/queue输入 ──
        should_run = True
        promotion = "steer"
        
        while should_run:
            # ── 内层while循环：执行provider turn ──
            needs_continuation = True
            step = 1
            
            while needs_continuation and step <= max_steps:
                logger.info(f"Step {step}/{max_steps}")
                
                # 执行单个turn
                turn_result, turn_context = self._run_turn(
                    session_id=session_id,
                    messages=messages,
                    system_prompt=system_prompt,
                    step=step,
                    promotion=promotion,
                    context_limit=context_limit,
                )
                
                # 处理turn结果
                if turn_result == TurnResult.SETTLE:
                    needs_continuation = False
                    should_run = False
                    break
                elif turn_result == TurnResult.FAIL:
                    logger.error(f"Turn failed: {turn_context.error}")
                    return RunnerResult(
                        success=False,
                        errors=[turn_context.error or "Turn failed"],
                        steps=step,
                    )
                elif turn_result == TurnResult.INTERRUPT:
                    return RunnerResult(
                        success=False,
                        errors=["Interrupted"],
                        steps=step,
                    )
                elif turn_result == TurnResult.CONTINUE:
                    # 检查是否需要工具执行
                    if turn_context.tool_calls:
                        # 执行工具
                        tool_results = self._execute_tools(
                            turn_context.tool_calls,
                            session_id,
                        )
                        # 将工具结果添加到消息
                        messages.extend(tool_results)
                    
                    # 检查是否需要继续
                    needs_continuation = turn_context.needs_continuation
                    
                    # 如果不需要继续，检查是否有steer输入
                    if not needs_continuation and self.session_input:
                        needs_continuation = self.session_input.has_pending(
                            session_id,
                            "steer"
                        )
                
                step += 1
                promotion = "steer"  # 后续都用steer
            
            # ── 外层：检查queue输入 ──
            if self.session_input:
                should_run = self.session_input.has_pending(session_id, "queue")
                if should_run:
                    promotion = "queue"
                    # 投递下一个queue输入
                    queued = self.session_input.promote_next_queued(session_id)
                    if queued:
                        messages.append({
                            "role": "user",
                            "content": queued.content,
                        })
            else:
                should_run = False
        
        logger.info(f"Session runner completed for session: {session_id}")
        
        return RunnerResult(
            success=True,
            output=messages[-1] if messages else None,
            steps=step - 1,
            metadata={"session_id": session_id},
        )
    
    def _run_turn(
        self,
        session_id: str,
        messages: List[Dict[str, Any]],
        system_prompt: str,
        step: int,
        promotion: str,
        context_limit: int,
    ) -> Tuple[TurnResult, TurnContext]:
        """
        执行单个provider turn
        
        Returns:
            (TurnResult, TurnContext)
        """
        turn_context = TurnContext(
            step=step,
            messages=messages,
            tool_definitions=[],
        )
        
        try:
            # 1. 检查是否需要compaction
            if self.compaction:
                compaction_result = self.compaction.compact_if_needed(
                    messages,
                    context_limit=context_limit,
                )
                if compaction_result.compacted:
                    # compaction完成，重建上下文
                    messages = self._rebuild_context(
                        compaction_result.summary,
                        compaction_result.recent_context,
                        messages,
                    )
                    turn_context.messages = messages
            
            # 2. 获取工具定义
            if self.tool_registry:
                materialization = self.tool_registry.materialize()
                turn_context.tool_definitions = materialization.to_definitions()
            
            # 3. 调用LLM
            if not self.llm_client:
                turn_context.error = "No LLM client"
                return TurnResult.FAIL, turn_context
            
            # 构建请求消息
            request_messages = self._build_messages(
                messages,
                system_prompt,
                turn_context.tool_definitions,
            )
            
            # 调用LLM
            response = self.llm_client.chat(
                messages=request_messages,
                temperature=0.1,
                max_tokens=2000,
            )
            
            # 4. 解析响应
            parsed = self._parse_response(response)
            
            # 5. 处理工具调用
            if parsed.get("tool_calls"):
                turn_context.tool_calls = parsed["tool_calls"]
                turn_context.needs_continuation = True
                
                # 添加助手消息
                messages.append({
                    "role": "assistant",
                    "content": parsed.get("content", ""),
                    "tool_calls": parsed["tool_calls"],
                })
                
                return TurnResult.CONTINUE, turn_context
            else:
                # 无工具调用，任务完成
                messages.append({
                    "role": "assistant",
                    "content": parsed.get("content", ""),
                })
                
                turn_context.needs_continuation = False
                return TurnResult.SETTLE, turn_context
        
        except Exception as e:
            logger.error(f"Turn failed: {e}")
            turn_context.error = str(e)
            
            # 重试逻辑
            if step < self.max_retries:
                turn_context.needs_continuation = True
                return TurnResult.CONTINUE, turn_context
            else:
                return TurnResult.FAIL, turn_context
    
    def _build_messages(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: str,
        tool_definitions: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """构建LLM消息列表"""
        result = []
        
        # 添加系统提示
        if system_prompt:
            result.append({"role": "system", "content": system_prompt})
        
        # 添加历史消息
        result.extend(messages)
        
        return result
    
    def _parse_response(self, response: str) -> Dict[str, Any]:
        """解析LLM响应"""
        try:
            # 尝试解析JSON
            json_match = re.search(r'\{.*\}|\[.*\]', response, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                return data
            
            # 返回纯文本
            return {"content": response, "tool_calls": []}
        except Exception:
            return {"content": response, "tool_calls": []}
    
    def _execute_tools(
        self,
        tool_calls: List[Dict[str, Any]],
        session_id: str,
    ) -> List[Dict[str, Any]]:
        """执行工具调用"""
        results = []
        
        for call in tool_calls:
            tool_name = call.get("name", "")
            tool_args = call.get("arguments", {})
            call_id = call.get("id", f"call_{int(time.time() * 1000)}")
            
            try:
                if self.tool_registry:
                    from .tool_registry import ToolCall, get_tool_registry
                    
                    tool_call = ToolCall(
                        id=call_id,
                        name=tool_name,
                        arguments=tool_args,
                        session_id=session_id,
                    )
                    
                    materialization = self.tool_registry.materialize()
                    result = materialization.settle(tool_call)
                    
                    results.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": json.dumps(result.result) if result.success else f"Error: {result.error}",
                    })
                else:
                    results.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": json.dumps({"error": "No tool registry"}),
                    })
            except Exception as e:
                results.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": json.dumps({"error": str(e)}),
                })
        
        return results
    
    def _rebuild_context(
        self,
        summary: str,
        recent_context: str,
        messages: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """重建上下文（compaction后）"""
        # 保留最近的消息
        recent_messages = messages[-8:] if len(messages) > 8 else messages
        
        # 创建compaction消息
        compaction_message = {
            "role": "system",
            "content": f"""<conversation-checkpoint>
以下是之前对话的摘要和序列化记录...

<summary>
{summary}
</summary>

<recent-context>
{recent_context}
</recent-context>
</conversation-checkpoint>""",
        }
        
        return [compaction_message] + recent_messages


# 全局实例
_session_runner: Optional[SessionRunner] = None


def get_session_runner(
    llm_client: Any = None,
    tool_registry: Any = None,
    session_input: Any = None,
    compaction: Any = None,
) -> SessionRunner:
    """获取SessionRunner单例"""
    global _session_runner
    if _session_runner is None:
        _session_runner = SessionRunner(
            llm_client=llm_client,
            tool_registry=tool_registry,
            session_input=session_input,
            compaction=compaction,
        )
    return _session_runner
