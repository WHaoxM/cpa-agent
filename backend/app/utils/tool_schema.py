"""Tool Schema验证和结构化输出

借鉴OpenCode的Tool系统架构，为CPA-Agent提供：
1. Tool Schema定义和验证
2. 结构化输出支持
3. Tool注册和管理
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Type, get_type_hints
from enum import Enum


class ToolPermission(Enum):
    """Tool权限级别"""
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


@dataclass
class ToolSchema:
    """Tool Schema定义"""
    name: str
    description: str
    input_schema: Dict[str, Any]
    output_schema: Dict[str, Any]
    permission: ToolPermission = ToolPermission.ALLOW
    
    def validate_input(self, data: Dict[str, Any]) -> bool:
        """验证输入数据是否符合schema"""
        # 简单验证：检查必需字段
        required = self.input_schema.get('required', [])
        properties = self.input_schema.get('properties', {})
        
        for field_name in required:
            if field_name not in data:
                return False
        
        # 验证字段类型
        for field_name, value in data.items():
            if field_name in properties:
                expected_type = properties[field_name].get('type')
                if expected_type == 'string' and not isinstance(value, str):
                    return False
                elif expected_type == 'integer' and not isinstance(value, int):
                    return False
                elif expected_type == 'number' and not isinstance(value, (int, float)):
                    return False
                elif expected_type == 'boolean' and not isinstance(value, bool):
                    return False
        
        return True
    
    def validate_output(self, data: Any) -> bool:
        """验证输出数据是否符合schema"""
        # 简单验证
        return True


@dataclass
class ToolDefinition:
    """Tool完整定义"""
    schema: ToolSchema
    execute: Callable
    to_model_output: Optional[Callable] = None
    to_structured_output: Optional[Callable] = None
    
    def execute_with_validation(self, input_data: Dict[str, Any], context: Any = None) -> Any:
        """执行Tool并验证输入输出"""
        if not self.schema.validate_input(input_data):
            raise ValueError(f"Invalid input for tool {self.schema.name}")
        
        result = self.execute(input_data, context)
        
        if not self.schema.validate_output(result):
            raise ValueError(f"Invalid output from tool {self.schema.name}")
        
        return result


class ToolRegistry:
    """Tool注册表"""
    
    def __init__(self):
        self._tools: Dict[str, ToolDefinition] = {}
        self._permissions: Dict[str, ToolPermission] = {}
    
    def register(
        self,
        name: str,
        description: str,
        input_schema: Dict[str, Any],
        output_schema: Dict[str, Any],
        execute: Callable,
        permission: ToolPermission = ToolPermission.ALLOW,
        to_model_output: Optional[Callable] = None,
        to_structured_output: Optional[Callable] = None,
    ) -> ToolDefinition:
        """注册Tool"""
        schema = ToolSchema(
            name=name,
            description=description,
            input_schema=input_schema,
            output_schema=output_schema,
            permission=permission,
        )
        
        tool_def = ToolDefinition(
            schema=schema,
            execute=execute,
            to_model_output=to_model_output,
            to_structured_output=to_structured_output,
        )
        
        self._tools[name] = tool_def
        self._permissions[name] = permission
        
        return tool_def
    
    def get(self, name: str) -> Optional[ToolDefinition]:
        """获取Tool定义"""
        return self._tools.get(name)
    
    def list_tools(self, permission_filter: Optional[ToolPermission] = None) -> List[ToolDefinition]:
        """列出所有Tool"""
        tools = list(self._tools.values())
        
        if permission_filter:
            tools = [t for t in tools if self._permissions.get(t.schema.name) == permission_filter]
        
        return tools
    
    def get_definitions(self) -> List[Dict[str, Any]]:
        """获取所有Tool定义（用于LLM）"""
        return [
            {
                'name': tool.schema.name,
                'description': tool.schema.description,
                'input_schema': tool.schema.input_schema,
                'output_schema': tool.schema.output_schema,
            }
            for tool in self._tools.values()
        ]
    
    def execute(self, name: str, input_data: Dict[str, Any], context: Any = None) -> Any:
        """执行Tool"""
        tool = self._tools.get(name)
        if not tool:
            raise ValueError(f"Tool {name} not found")
        
        return tool.execute_with_validation(input_data, context)


# 全局Tool注册表
_tool_registry: Optional[ToolRegistry] = None


def get_tool_registry() -> ToolRegistry:
    """获取Tool注册表单例"""
    global _tool_registry
    if _tool_registry is None:
        _tool_registry = ToolRegistry()
    return _tool_registry


def register_tool(
    name: str,
    description: str,
    input_schema: Dict[str, Any],
    output_schema: Dict[str, Any],
    execute: Callable,
    permission: ToolPermission = ToolPermission.ALLOW,
) -> ToolDefinition:
    """注册Tool的便捷函数"""
    return get_tool_registry().register(
        name=name,
        description=description,
        input_schema=input_schema,
        output_schema=output_schema,
        execute=execute,
        permission=permission,
    )
