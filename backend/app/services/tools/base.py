"""
Tool 基类
所有 ReAct Agent 工具的抽象基类
"""

from abc import ABC, abstractmethod
from typing import Dict, Any


class BaseTool(ABC):
    """Tool 基类"""

    @property
    @abstractmethod
    def name(self) -> str:
        """工具名称 (英文, snake_case)"""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """工具描述 (供 LLM System Prompt 使用)"""
        ...

    @property
    @abstractmethod
    def parameters(self) -> Dict[str, str]:
        """参数定义 {参数名: 参数说明}"""
        ...

    @abstractmethod
    def execute(self, **kwargs) -> str:
        """
        执行工具

        Returns:
            文本格式的结果 (供 LLM 理解)
        """
        ...

    def to_definition(self) -> Dict[str, Any]:
        """导出工具定义 (用于 ReAct Agent 的 System Prompt)"""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters
        }
