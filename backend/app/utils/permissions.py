"""三态权限模型

提供allow/deny/ask三种权限效果，支持通配符匹配。
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional


class PermissionEffect(Enum):
    """权限效果"""
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class SavedReply(Enum):
    """已保存的回复"""
    ONCE = "once"
    ALWAYS = "always"
    REJECT = "reject"


@dataclass
class PermissionRule:
    """权限规则"""
    action: str           # 工具动作（如 "tool.*", "file.write"）
    effect: PermissionEffect
    pattern: Optional[str] = None  # 通配符模式
    description: str = ""
    
    def matches(self, action: str) -> bool:
        """检查是否匹配"""
        if self.pattern:
            return fnmatch.fnmatch(action, self.pattern)
        return fnmatch.fnmatch(action, self.action)


@dataclass
class PermissionRequest:
    """权限请求"""
    action: str
    context: Dict[str, Any] = field(default_factory=dict)
    resource: Optional[str] = None
    timestamp: float = field(default_factory=lambda: __import__('time').time())


@dataclass
class PermissionResponse:
    """权限响应"""
    effect: PermissionEffect
    saved: Optional[SavedReply] = None
    reason: str = ""


class PermissionSystem:
    """权限系统"""
    
    def __init__(self):
        self._rules: List[PermissionRule] = []
        self._saved_replies: Dict[str, SavedReply] = {}
        self._pending_requests: Dict[str, PermissionRequest] = {}
        self._callbacks: Dict[str, Callable] = {}
    
    def evaluate(self, action: str, context: Dict[str, Any] = None) -> PermissionEffect:
        """
        评估权限
        
        Args:
            action: 工具动作
            context: 上下文
        
        Returns:
            PermissionEffect
        """
        context = context or {}
        
        # 检查已保存的回复
        saved = self._saved_replies.get(action)
        if saved == SavedReply.ALWAYS:
            return PermissionEffect.ALLOW
        elif saved == SavedReply.REJECT:
            return PermissionEffect.DENY
        
        # 检查规则
        for rule in self._rules:
            if rule.matches(action):
                return rule.effect
        
        # 默认询问
        return PermissionEffect.ASK
    
    def save_reply(self, action: str, reply: SavedReply):
        """保存回复"""
        self._saved_replies[action] = reply
    
    def clear_saved_reply(self, action: str):
        """清除已保存的回复"""
        self._saved_replies.pop(action, None)
    
    def add_rule(self, rule: PermissionRule):
        """添加规则"""
        self._rules.append(rule)
        # 按优先级排序（更具体的规则优先）
        self._rules.sort(key=lambda r: len(r.pattern or r.action), reverse=True)
    
    def remove_rule(self, action: str):
        """移除规则"""
        self._rules = [r for r in self._rules if r.action != action]
    
    def request_permission(self, request: PermissionRequest) -> PermissionResponse:
        """
        请求权限
        
        Args:
            request: 权限请求
        
        Returns:
            PermissionResponse
        """
        effect = self.evaluate(request.action, request.context)
        
        if effect == PermissionEffect.ASK:
            # 保存待处理请求
            self._pending_requests[request.action] = request
            # 触发回调
            self._trigger_callback("ask", request)
        
        return PermissionResponse(effect=effect)
    
    def respond_to_request(self, action: str, effect: PermissionEffect, saved: SavedReply = None):
        """响应权限请求"""
        if action in self._pending_requests:
            request = self._pending_requests.pop(action)
            response = PermissionResponse(effect=effect, saved=saved)
            
            # 保存回复
            if saved:
                self.save_reply(action, saved)
            
            # 触发回调
            self._trigger_callback("respond", (request, response))
    
    def register_callback(self, event: str, callback: Callable):
        """注册回调"""
        if event not in self._callbacks:
            self._callbacks[event] = []
        self._callbacks[event].append(callback)
    
    def _trigger_callback(self, event: str, data: Any):
        """触发回调"""
        for callback in self._callbacks.get(event, []):
            try:
                callback(data)
            except Exception as e:
                print(f"Callback error: {e}")
    
    def get_pending_requests(self) -> List[PermissionRequest]:
        """获取待处理请求"""
        return list(self._pending_requests.values())
    
    def clear_all(self):
        """清除所有规则和回复"""
        self._rules.clear()
        self._saved_replies.clear()
        self._pending_requests.clear()
    
    def get_rules(self) -> List[Dict[str, Any]]:
        """获取所有规则"""
        return [
            {
                "action": rule.action,
                "effect": rule.effect.value,
                "pattern": rule.pattern,
                "description": rule.description,
            }
            for rule in self._rules
        ]
    
    def get_saved_replies(self) -> Dict[str, str]:
        """获取已保存的回复"""
        return {k: v.value for k, v in self._saved_replies.items()}


class PermissionMiddleware:
    """权限中间件"""
    
    def __init__(self, permission_system: PermissionSystem):
        self.permission_system = permission_system
    
    def check_tool_permission(self, tool_name: str, context: Dict[str, Any] = None) -> bool:
        """
        检查工具权限
        
        Args:
            tool_name: 工具名称
            context: 上下文
        
        Returns:
            是否允许
        """
        effect = self.permission_system.evaluate(f"tool.{tool_name}", context)
        
        if effect == PermissionEffect.ALLOW:
            return True
        elif effect == PermissionEffect.DENY:
            return False
        else:
            # 需要询问用户
            return self._ask_user(tool_name, context)
    
    def _ask_user(self, tool_name: str, context: Dict[str, Any]) -> bool:
        """询问用户"""
        # 这里应该实现与用户的交互
        # 暂时返回False
        print(f"Permission required for tool: {tool_name}")
        return False


# 预定义规则
DEFAULT_RULES = [
    PermissionRule(
        action="tool.read",
        effect=PermissionEffect.ALLOW,
        description="允许读取工具",
    ),
    PermissionRule(
        action="tool.search",
        effect=PermissionEffect.ALLOW,
        description="允许搜索工具",
    ),
    PermissionRule(
        action="tool.write",
        effect=PermissionEffect.ASK,
        description="写入工具需要询问",
    ),
    PermissionRule(
        action="tool.delete",
        effect=PermissionEffect.DENY,
        description="禁止删除工具",
    ),
]


# 全局实例
_permission_system: Optional[PermissionSystem] = None


def get_permission_system() -> PermissionSystem:
    """获取权限系统单例"""
    global _permission_system
    if _permission_system is None:
        _permission_system = PermissionSystem()
        # 添加默认规则
        for rule in DEFAULT_RULES:
            _permission_system.add_rule(rule)
    return _permission_system
