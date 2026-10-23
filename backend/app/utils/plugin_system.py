"""插件系统

提供可扩展性，支持：
- 插件定义和注册
- 插件Hook机制
- 插件生命周期管理
- 插件上下文
"""

from __future__ import annotations

import importlib
import inspect
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional


class HookType(Enum):
    """Hook类型"""
    CHAT_MESSAGE_BEFORE = "chat.message.before"
    CHAT_MESSAGE_AFTER = "chat.message.after"
    CHAT_PARAMS = "chat.params"
    CHAT_SYSTEM_PROMPT = "chat.system_prompt"
    
    TOOL_EXECUTE_BEFORE = "tool.execute.before"
    TOOL_EXECUTE_AFTER = "tool.execute.after"
    TOOL_REGISTER = "tool.register"
    
    PERMISSION_ASK = "permission.ask"
    PERMISSION_GRANT = "permission.grant"
    
    SESSION_CREATED = "session.created"
    SESSION_CLOSED = "session.closed"
    SESSION_COMPACT = "session.compact"
    
    FILE_UPLOADED = "file.uploaded"
    FILE_PROCESSED = "file.processed"
    
    ROLE_ACTIVATED = "role.activated"
    ROLE_DEACTIVATED = "role.deactivated"
    
    PLUGIN_LOADED = "plugin.loaded"
    PLUGIN_UNLOADED = "plugin.unloaded"


@dataclass
class PluginHook:
    """插件Hook"""
    hook_type: HookType
    handler: Callable
    priority: int = 0
    description: str = ""


@dataclass
class PluginContext:
    """插件上下文"""
    agent: Any = None
    catalog: Any = None
    command: Any = None
    integration: Any = None
    reference: Any = None
    skill: Any = None
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "agent": self.agent,
            "catalog": self.catalog,
            "command": self.command,
            "integration": self.integration,
            "reference": self.reference,
            "skill": self.skill,
        }


@dataclass
class Plugin:
    """插件定义"""
    name: str
    version: str = "1.0.0"
    description: str = ""
    author: str = ""
    hooks: List[PluginHook] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    context: Optional[PluginContext] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "author": self.author,
            "hooks_count": len(self.hooks),
            "metadata": self.metadata,
        }


class PluginRegistry:
    """插件注册表"""
    
    def __init__(self):
        self._plugins: Dict[str, Plugin] = {}
        self._hooks: Dict[HookType, List[PluginHook]] = {}
        self._load_order: List[str] = []
    
    def register(self, plugin: Plugin):
        """注册插件"""
        self._plugins[plugin.name] = plugin
        self._load_order.append(plugin.name)
        
        for hook in plugin.hooks:
            if hook.hook_type not in self._hooks:
                self._hooks[hook.hook_type] = []
            self._hooks[hook.hook_type].append(hook)
            self._hooks[hook.hook_type].sort(key=lambda h: h.priority, reverse=True)
    
    def unregister(self, name: str):
        """注销插件"""
        if name in self._plugins:
            plugin = self._plugins[name]
            for hook in plugin.hooks:
                if hook.hook_type in self._hooks:
                    self._hooks[hook.hook_type] = [
                        h for h in self._hooks[hook.hook_type]
                        if h.handler != hook.handler
                    ]
            del self._plugins[name]
            self._load_order.remove(name)
    
    def get_plugin(self, name: str) -> Optional[Plugin]:
        """获取插件"""
        return self._plugins.get(name)
    
    def list_plugins(self) -> List[Dict[str, Any]]:
        """列出所有插件"""
        return [plugin.to_dict() for plugin in self._plugins.values()]
    
    async def execute_hook(self, hook_type: HookType, *args, **kwargs) -> Any:
        """执行Hook"""
        hooks = self._hooks.get(hook_type, [])
        result = kwargs.get("default")
        
        for hook in hooks:
            try:
                if inspect.iscoroutinefunction(hook.handler):
                    result = await hook.handler(result, *args, **kwargs)
                else:
                    result = hook.handler(result, *args, **kwargs)
            except Exception as e:
                print(f"Hook error for {hook_type.value}: {e}")
        
        return result
    
    def get_hooks_for_type(self, hook_type: HookType) -> List[PluginHook]:
        """获取指定类型的Hook"""
        return self._hooks.get(hook_type, [])
    
    def clear(self):
        """清除所有插件"""
        self._plugins.clear()
        self._hooks.clear()
        self._load_order.clear()


class PluginLoader:
    """插件加载器"""
    
    def __init__(self, registry: PluginRegistry):
        self.registry = registry
    
    def load_from_module(self, module_path: str) -> Optional[Plugin]:
        """从模块加载插件"""
        try:
            module = importlib.import_module(module_path)
            if hasattr(module, "plugin"):
                plugin = module.plugin
                if isinstance(plugin, Plugin):
                    self.registry.register(plugin)
                    return plugin
        except Exception as e:
            print(f"Failed to load plugin from {module_path}: {e}")
        return None
    
    def load_from_dict(self, data: Dict[str, Any]) -> Optional[Plugin]:
        """从字典加载插件"""
        try:
            plugin = Plugin(
                name=data["name"],
                version=data.get("version", "1.0.0"),
                description=data.get("description", ""),
                author=data.get("author", ""),
                metadata=data.get("metadata", {}),
            )
            
            # 加载hooks
            for hook_data in data.get("hooks", []):
                hook_type = HookType(hook_data["type"])
                handler = self._create_handler(hook_data.get("handler"))
                if handler:
                    plugin.hooks.append(PluginHook(
                        hook_type=hook_type,
                        handler=handler,
                        priority=hook_data.get("priority", 0),
                        description=hook_data.get("description", ""),
                    ))
            
            self.registry.register(plugin)
            return plugin
        except Exception as e:
            print(f"Failed to load plugin from dict: {e}")
        return None
    
    def _create_handler(self, handler_data: Any) -> Optional[Callable]:
        """创建handler"""
        if callable(handler_data):
            return handler_data
        elif isinstance(handler_data, str):
            # 尝试作为函数路径加载
            try:
                module_path, func_name = handler_data.rsplit(".", 1)
                module = importlib.import_module(module_path)
                return getattr(module, func_name)
            except Exception:
                pass
        return None


class PluginContext:
    """插件上下文"""
    
    def __init__(self):
        self._data: Dict[str, Any] = {}
    
    def set(self, key: str, value: Any):
        """设置值"""
        self._data[key] = value
    
    def get(self, key: str, default: Any = None) -> Any:
        """获取值"""
        return self._data.get(key, default)
    
    def has(self, key: str) -> bool:
        """检查是否有值"""
        return key in self._data
    
    def remove(self, key: str):
        """移除值"""
        self._data.pop(key, None)
    
    def clear(self):
        """清除所有值"""
        self._data.clear()
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return self._data.copy()


# 预定义插件示例

def create_example_plugin() -> Plugin:
    """创建示例插件"""
    def before_message(handler, message, *args, **kwargs):
        """消息发送前处理"""
        print(f"Before message: {message}")
        return message
    
    def after_message(handler, response, *args, **kwargs):
        """消息发送后处理"""
        print(f"After message: {response}")
        return response
    
    return Plugin(
        name="example-plugin",
        version="1.0.0",
        description="示例插件",
        author="CPA-Agent",
        hooks=[
            PluginHook(
                hook_type=HookType.CHAT_MESSAGE_BEFORE,
                handler=before_message,
                priority=10,
            ),
            PluginHook(
                hook_type=HookType.CHAT_MESSAGE_AFTER,
                handler=after_message,
                priority=10,
            ),
        ],
    )


# 全局实例
_plugin_registry: Optional[PluginRegistry] = None
_plugin_loader: Optional[PluginLoader] = None


def get_plugin_registry() -> PluginRegistry:
    """获取插件注册表单例"""
    global _plugin_registry
    if _plugin_registry is None:
        _plugin_registry = PluginRegistry()
    return _plugin_registry


def get_plugin_loader() -> PluginLoader:
    """获取插件加载器单例"""
    global _plugin_loader
    if _plugin_loader is None:
        _plugin_loader = PluginLoader(get_plugin_registry())
    return _plugin_loader
