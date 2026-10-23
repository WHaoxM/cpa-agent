"""Tool Registry - 参考OpenCode架构

核心设计：
1. 工具注册和管理
2. 权限过滤
3. Materialization快照
4. 工具执行和结果持久化
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional
import threading
import logging

logger = logging.getLogger(__name__)


class ToolPermission(Enum):
    """工具权限"""
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


@dataclass
class ToolDefinition:
    """工具定义"""
    name: str
    description: str
    input_schema: Dict[str, Any]
    output_schema: Dict[str, Any]
    permission: ToolPermission = ToolPermission.ALLOW
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolRegistration:
    """工具注册"""
    definition: ToolDefinition
    executor: Callable
    identity: str = ""  # 工具身份标识（用于检测过期调用）
    registered_at: float = field(default_factory=time.time)


@dataclass
class ToolCall:
    """工具调用"""
    id: str
    name: str
    arguments: Dict[str, Any]
    session_id: str = ""
    agent_id: str = ""
    timestamp: float = field(default_factory=time.time)


@dataclass
class ToolResult:
    """工具执行结果"""
    call_id: str
    success: bool
    result: Any = None
    error: Optional[str] = None
    output_path: Optional[str] = None  # 大输出文件路径


class ToolRegistry:
    """
    Tool Registry - 参考OpenCode ToolRegistry
    
    职责：
    1. 管理工具注册（应用层 + Location层）
    2. 权限过滤
    3. Materialization快照
    4. 工具执行和结果持久化
    """
    
    def __init__(self):
        self._lock = threading.Lock()
        self._application_tools: Dict[str, ToolRegistration] = {}
        self._location_tools: Dict[str, ToolRegistration] = {}
        self._tool_audit: List[Dict[str, Any]] = []
    
    def register(
        self,
        name: str,
        description: str,
        input_schema: Dict[str, Any],
        output_schema: Dict[str, Any],
        executor: Callable,
        permission: ToolPermission = ToolPermission.ALLOW,
        scope: str = "application",  # "application" 或 "location"
    ) -> ToolDefinition:
        """
        注册工具
        
        Args:
            name: 工具名称
            description: 工具描述
            input_schema: 输入Schema
            output_schema: 输出Schema
            executor: 执行函数
            permission: 权限
            scope: 作用域（application/location）
        
        Returns:
            ToolDefinition
        """
        definition = ToolDefinition(
            name=name,
            description=description,
            input_schema=input_schema,
            output_schema=output_schema,
            permission=permission,
        )
        
        registration = ToolRegistration(
            definition=definition,
            executor=executor,
            identity=f"{name}_{int(time.time() * 1000)}",
        )
        
        with self._lock:
            if scope == "location":
                self._location_tools[name] = registration
            else:
                self._application_tools[name] = registration
        
        logger.info(f"Registered tool: {name} (scope={scope})")
        return definition
    
    def materialize(
        self,
        permissions: Optional[List[str]] = None,
    ) -> "ToolMaterialization":
        """
        创建工具快照（参考OpenCode Materialization）
        
        每次turn开始时调用，冻结当前工具集
        """
        with self._lock:
            # 合并应用层和Location层工具（Location优先）
            registrations = dict(self._application_tools)
            registrations.update(self._location_tools)
            
            # 权限过滤
            if permissions:
                registrations = {
                    name: reg for name, reg in registrations.items()
                    if not self._is_disabled(name, permissions)
                }
            
            # 创建快照
            definitions = [
                reg.definition for reg in registrations.values()
            ]
            
            return ToolMaterialization(
                definitions=definitions,
                registrations=registrations,
                snapshot_time=time.time(),
            )
    
    def _is_disabled(self, name: str, permissions: List[str]) -> bool:
        """检查工具是否被禁用"""
        # 简单的权限检查逻辑
        return name in permissions
    
    def get_tool(self, name: str) -> Optional[ToolRegistration]:
        """获取工具注册"""
        with self._lock:
            return self._location_tools.get(name) or self._application_tools.get(name)
    
    def list_tools(self) -> List[ToolDefinition]:
        """列出所有工具"""
        with self._lock:
            registrations = dict(self._application_tools)
            registrations.update(self._location_tools)
            return [reg.definition for reg in registrations.values()]
    
    def audit_tool_call(self, call: ToolCall, result: ToolResult):
        """审计工具调用"""
        self._tool_audit.append({
            "call": call,
            "result": result,
            "timestamp": time.time(),
        })


class ToolMaterialization:
    """
    工具快照 - 参考OpenCode Materialization
    
    每次turn开始时创建，capture当时的注册状态
    """
    
    def __init__(
        self,
        definitions: List[ToolDefinition],
        registrations: Dict[str, ToolRegistration],
        snapshot_time: float,
    ):
        self.definitions = definitions
        self._registrations = registrations
        self._snapshot_time = snapshot_time
    
    def settle(self, call: ToolCall) -> ToolResult:
        """
        执行工具调用
        
        Args:
            call: 工具调用
        
        Returns:
            ToolResult
        """
        registration = self._registrations.get(call.name)
        
        if not registration:
            return ToolResult(
                call_id=call.id,
                success=False,
                error=f"Unknown tool: {call.name}",
            )
        
        # 检查工具是否过期（在snapshot之后被重新注册）
        if registration.registered_at > self._snapshot_time:
            return ToolResult(
                call_id=call.id,
                success=False,
                error=f"Stale tool call: {call.name} was re-registered after snapshot",
            )
        
        try:
            # 执行工具
            result = registration.executor(**call.arguments)
            
            return ToolResult(
                call_id=call.id,
                success=True,
                result=result,
            )
        except Exception as e:
            logger.error(f"Tool execution failed: {call.name}, error: {e}")
            return ToolResult(
                call_id=call.id,
                success=False,
                error=str(e),
            )
    
    def to_definitions(self) -> List[Dict[str, Any]]:
        """转换为LLM工具定义格式"""
        return [
            {
                "name": defn.name,
                "description": defn.description,
                "input_schema": defn.input_schema,
            }
            for defn in self.definitions
        ]


# 全局实例
_tool_registry: Optional[ToolRegistry] = None


def get_tool_registry() -> ToolRegistry:
    """获取ToolRegistry单例"""
    global _tool_registry
    if _tool_registry is None:
        _tool_registry = ToolRegistry()
    return _tool_registry


def register_tool(
    name: str,
    description: str,
    input_schema: Dict[str, Any],
    output_schema: Dict[str, Any],
    executor: Callable,
    permission: ToolPermission = ToolPermission.ALLOW,
) -> ToolDefinition:
    """注册工具的便捷函数"""
    return get_tool_registry().register(
        name=name,
        description=description,
        input_schema=input_schema,
        output_schema=output_schema,
        executor=executor,
        permission=permission,
    )
