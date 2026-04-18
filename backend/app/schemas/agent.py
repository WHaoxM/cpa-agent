"""
Agent 相关 Schema
"""

from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """聊天请求"""
    message: str = Field(..., description='用户消息')
    session_id: Optional[str] = Field(None, description='会话ID')
    student_id: Optional[str] = Field(None, description='学生ID')


class ToolCall(BaseModel):
    """工具调用记录"""
    tool: str
    parameters: Dict[str, Any]
    result: Optional[str] = None
    elapsed_ms: Optional[float] = None


class ChatResponse(BaseModel):
    """聊天响应"""
    reply: str
    session_id: str
    tool_calls: Optional[List[ToolCall]] = None
    thinking_steps: Optional[List[str]] = None
    iterations: int = 1
    elapsed_seconds: Optional[float] = None


class SessionCreateRequest(BaseModel):
    """创建会话请求"""
    student_id: Optional[str] = Field(None, description='学生ID')
    title: Optional[str] = Field(None, description='会话标题')


class SessionCreateResponse(BaseModel):
    """创建会话响应"""
    session_id: str
    student_id: Optional[str] = None
    title: Optional[str] = None
    created_at: str


class MessageItem(BaseModel):
    """消息项"""
    message_id: str
    role: str
    content: str
    tool_calls: Optional[List[Dict[str, Any]]] = None
    created_at: str


class SessionHistoryResponse(BaseModel):
    """会话历史响应"""
    session_id: str
    student_id: Optional[str] = None
    title: Optional[str] = None
    status: str
    message_count: int
    messages: List[MessageItem]
    created_at: str
    updated_at: str


class GraphQueryRequest(BaseModel):
    """图谱查询请求"""
    query: str = Field(..., description='自然语言查询')
    category: Optional[str] = Field(None, description='技能分类过滤')


class GraphQueryResponse(BaseModel):
    """图谱查询响应"""
    query: str
    nodes: List[Dict[str, Any]]
    edges: List[Dict[str, Any]]
    answer: Optional[str] = None
