"""Agent Loop V2 - 参考OpenCode架构

核心设计：
1. 双层while循环：外层处理steer/queue，内层执行provider turn
2. needsContinuation控制是否需要继续
3. 步骤限制和错误恢复
4. 工具执行等待
5. 自动重试机制
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple
import logging

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────
# 状态定义
# ─────────────────────────────────────────────────────────

class AgentState(Enum):
    """Agent状态"""
    IDLE = "idle"
    RUNNING = "running"
    WAITING_TOOL = "waiting_tool"
    NEEDS_CONTINUATION = "needs_continuation"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class TurnResult(Enum):
    """Turn结果"""
    CONTINUE = "continue"  # 需要继续下一个turn
    SETTLE = "settle"      # 任务完成
    FAIL = "fail"          # 失败
    INTERRUPT = "interrupt"  # 被中断


# ─────────────────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────────────────

@dataclass
class AgentContext:
    """Agent上下文"""
    session_id: str
    state: AgentState = AgentState.IDLE
    step: int = 0
    max_steps: int = 10
    history: List[Dict[str, Any]] = field(default_factory=list)
    tools: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TurnContext:
    """单次Turn上下文"""
    step: int
    messages: List[Dict[str, Any]]
    tools: List[Dict[str, Any]]
    needs_continuation: bool = False
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class AgentLoopResult:
    """Agent Loop结果"""
    success: bool
    output: Any = None
    steps: int = 0
    errors: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


# ─────────────────────────────────────────────────────────
# Agent Loop 核心
# ─────────────────────────────────────────────────────────

class AgentLoop:
    """
    Agent Loop核心 - 参考OpenCode架构
    
    双层while循环：
    - 外层：处理steer/queue输入
    - 内层：执行provider turn直到settled
    """
    
    def __init__(
        self,
        llm_client: Any = None,
        tool_executor: Optional[Callable] = None,
        max_steps: int = 10,
        max_retries: int = 3,
    ):
        self.llm_client = llm_client
        self.tool_executor = tool_executor
        self.max_steps = max_steps
        self.max_retries = max_retries
    
    def run(
        self,
        initial_messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        system_prompt: str = "",
        **kwargs,
    ) -> AgentLoopResult:
        """
        执行Agent Loop
        
        Args:
            initial_messages: 初始消息列表
            tools: 可用工具列表
            system_prompt: 系统提示
        
        Returns:
            AgentLoopResult
        """
        context = AgentContext(
            session_id=kwargs.get("session_id", f"session_{int(time.time())}"),
            max_steps=self.max_steps,
            tools=tools or [],
        )
        
        # 初始消息
        messages = list(initial_messages)
        
        # ── 外层while循环：处理输入 ──
        should_run = True
        while should_run:
            # ── 内层while循环：执行provider turn ──
            needs_continuation = True
            while needs_continuation and context.step < context.max_steps:
                context.step += 1
                logger.info(f"Step {context.step}/{context.max_steps}")
                
                # 执行单个turn
                turn_result, turn_context = self._run_turn(
                    context=context,
                    messages=messages,
                    system_prompt=system_prompt,
                )
                
                # 处理turn结果
                if turn_result == TurnResult.SETTLE:
                    needs_continuation = False
                    should_run = False
                    break
                elif turn_result == TurnResult.FAIL:
                    context.state = AgentState.FAILED
                    should_run = False
                    break
                elif turn_result == TurnResult.INTERRUPT:
                    context.state = AgentState.INTERRUPTED
                    should_run = False
                    break
                elif turn_result == TurnResult.CONTINUE:
                    # 检查是否需要工具执行
                    if turn_context.tool_calls:
                        context.state = AgentState.WAITING_TOOL
                        # 等待工具执行完成
                        tool_results = self._execute_tools(turn_context.tool_calls)
                        # 将工具结果添加到消息
                        messages.extend(tool_results)
                    needs_continuation = turn_context.needs_continuation
            
            # 检查是否还有pending输入
            should_run = False
        
        # 返回结果
        return AgentLoopResult(
            success=context.state in (AgentState.COMPLETED, AgentState.IDLE),
            output=messages[-1] if messages else None,
            steps=context.step,
            metadata=context.metadata,
        )
    
    def _run_turn(
        self,
        context: AgentContext,
        messages: List[Dict[str, Any]],
        system_prompt: str,
    ) -> Tuple[TurnResult, TurnContext]:
        """
        执行单个provider turn
        
        Returns:
            (TurnResult, TurnContext)
        """
        turn_context = TurnContext(
            step=context.step,
            messages=messages,
            tools=context.tools,
        )
        
        try:
            # 调用LLM
            if not self.llm_client:
                turn_context.error = "No LLM client"
                return TurnResult.FAIL, turn_context
            
            # 构建请求
            request_messages = self._build_messages(messages, system_prompt)
            
            # 调用LLM
            response = self.llm_client.chat(
                messages=request_messages,
                temperature=0.1,
                max_tokens=2000,
            )
            
            # 解析响应
            parsed = self._parse_response(response)
            
            if parsed.get("tool_calls"):
                # 有工具调用
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
            if context.step < self.max_retries:
                turn_context.needs_continuation = True
                return TurnResult.CONTINUE, turn_context
            else:
                return TurnResult.FAIL, turn_context
    
    def _build_messages(
        self,
        messages: List[Dict[str, Any]],
        system_prompt: str,
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
    
    def _execute_tools(self, tool_calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """执行工具调用"""
        results = []
        
        for call in tool_calls:
            tool_name = call.get("name", "")
            tool_args = call.get("arguments", {})
            call_id = call.get("id", "")
            
            try:
                if self.tool_executor:
                    result = self.tool_executor(tool_name, tool_args)
                    results.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": json.dumps(result) if isinstance(result, dict) else str(result),
                    })
                else:
                    results.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": json.dumps({"error": "No tool executor"}),
                    })
            except Exception as e:
                results.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": json.dumps({"error": str(e)}),
                })
        
        return results


# ─────────────────────────────────────────────────────────
# 简历解析专用Agent Loop
# ─────────────────────────────────────────────────────────

class ResumeAgentLoop(AgentLoop):
    """简历解析专用Agent Loop"""
    
    def __init__(self, llm_client: Any = None):
        super().__init__(llm_client=llm_client, max_steps=5)
    
    def parse_resume(
        self,
        resume_text: str,
        max_retries: int = 3,
    ) -> AgentLoopResult:
        """
        解析简历（带重试）
        
        Args:
            resume_text: 简历文本
            max_retries: 最大重试次数
        
        Returns:
            AgentLoopResult
        """
        for attempt in range(max_retries):
            try:
                # 初始消息
                initial_messages = [
                    {
                        "role": "user",
                        "content": f"请解析以下简历并提取关键信息：\n\n{resume_text[:3000]}",
                    }
                ]
                
                # 系统提示
                system_prompt = """你是一个专业的简历解析助手。请从简历中提取以下信息：
1. 基本信息（姓名、电话、邮箱）
2. 教育背景（学校、专业、学历、GPA）
3. 技能列表
4. 项目经历
5. 竞赛获奖

输出JSON格式：
{
  "name": "姓名",
  "phone": "电话",
  "email": "邮箱",
  "school": "学校",
  "major": "专业",
  "degree": "学历",
  "gpa": "GPA",
  "skills": ["技能1", "技能2"],
  "projects": [{"name": "项目名", "desc": "描述"}],
  "competitions": [{"name": "竞赛名", "level": "级别"}]
}"""
                
                # 执行Agent Loop
                result = self.run(
                    initial_messages=initial_messages,
                    system_prompt=system_prompt,
                )
                
                if result.success:
                    return result
                
            except Exception as e:
                logger.error(f"Attempt {attempt + 1} failed: {e}")
                if attempt < max_retries - 1:
                    time.sleep(1)  # 等待后重试
        
        return AgentLoopResult(
            success=False,
            errors=["All attempts failed"],
        )


# ─────────────────────────────────────────────────────────
# 全局实例
# ─────────────────────────────────────────────────────────

_resume_loop: Optional[ResumeAgentLoop] = None


def get_resume_agent_loop(llm_client: Any = None) -> ResumeAgentLoop:
    """获取简历解析Agent Loop单例"""
    global _resume_loop
    if _resume_loop is None:
        _resume_loop = ResumeAgentLoop(llm_client)
    return _resume_loop


def parse_resume_with_loop(
    resume_text: str,
    llm_client: Any = None,
    max_retries: int = 3,
) -> AgentLoopResult:
    """使用Agent Loop解析简历的便捷函数"""
    loop = get_resume_agent_loop(llm_client)
    return loop.parse_resume(resume_text, max_retries)
