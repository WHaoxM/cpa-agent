"""TaggedError模式

提供精确错误处理，每个错误有唯一类型标识。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional
import traceback


class ErrorTag(Enum):
    """错误标签"""
    VALIDATION = "validation"
    PERMISSION = "permission"
    NOT_FOUND = "not_found"
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    INTERNAL = "internal"
    NETWORK = "network"
    AUTH = "auth"
    TOOL = "tool"
    SESSION = "session"
    CONFIG = "config"


@dataclass
class TaggedError:
    """带标签的错误"""
    tag: ErrorTag
    message: str
    details: Optional[Any] = None
    cause: Optional[Exception] = None
    code: Optional[str] = None
    
    def __str__(self):
        base = f"[{self.tag.value}] {self.message}"
        if self.code:
            base = f"[{self.code}] {base}"
        return base
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        result = {
            "tag": self.tag.value,
            "message": self.message,
        }
        if self.details:
            result["details"] = self.details
        if self.code:
            result["code"] = self.code
        if self.cause:
            result["cause"] = str(self.cause)
        return result


class ValidationError(TaggedError):
    """验证错误"""
    def __init__(self, message: str, details: Any = None, code: str = None):
        super().__init__(
            tag=ErrorTag.VALIDATION,
            message=message,
            details=details,
            code=code or "VALIDATION_ERROR",
        )


class PermissionError(TaggedError):
    """权限错误"""
    def __init__(self, message: str, details: Any = None, code: str = None):
        super().__init__(
            tag=ErrorTag.PERMISSION,
            message=message,
            details=details,
            code=code or "PERMISSION_DENIED",
        )


class NotFoundError(TaggedError):
    """未找到错误"""
    def __init__(self, message: str, details: Any = None, code: str = None):
        super().__init__(
            tag=ErrorTag.NOT_FOUND,
            message=message,
            details=details,
            code=code or "NOT_FOUND",
        )


class TimeoutError(TaggedError):
    """超时错误"""
    def __init__(self, message: str, details: Any = None, code: str = None):
        super().__init__(
            tag=ErrorTag.TIMEOUT,
            message=message,
            details=details,
            code=code or "TIMEOUT",
        )


class RateLimitError(TaggedError):
    """速率限制错误"""
    def __init__(self, message: str, details: Any = None, code: str = None):
        super().__init__(
            tag=ErrorTag.RATE_LIMIT,
            message=message,
            details=details,
            code=code or "RATE_LIMIT_EXCEEDED",
        )


class InternalError(TaggedError):
    """内部错误"""
    def __init__(self, message: str, details: Any = None, code: str = None, cause: Exception = None):
        super().__init__(
            tag=ErrorTag.INTERNAL,
            message=message,
            details=details,
            code=code or "INTERNAL_ERROR",
            cause=cause,
        )


class NetworkError(TaggedError):
    """网络错误"""
    def __init__(self, message: str, details: Any = None, code: str = None):
        super().__init__(
            tag=ErrorTag.NETWORK,
            message=message,
            details=details,
            code=code or "NETWORK_ERROR",
        )


class AuthError(TaggedError):
    """认证错误"""
    def __init__(self, message: str, details: Any = None, code: str = None):
        super().__init__(
            tag=ErrorTag.AUTH,
            message=message,
            details=details,
            code=code or "AUTH_ERROR",
        )


class ToolError(TaggedError):
    """工具错误"""
    def __init__(self, message: str, details: Any = None, code: str = None):
        super().__init__(
            tag=ErrorTag.TOOL,
            message=message,
            details=details,
            code=code or "TOOL_ERROR",
        )


class SessionError(TaggedError):
    """会话错误"""
    def __init__(self, message: str, details: Any = None, code: str = None):
        super().__init__(
            tag=ErrorTag.SESSION,
            message=message,
            details=details,
            code=code or "SESSION_ERROR",
        )


class ConfigError(TaggedError):
    """配置错误"""
    def __init__(self, message: str, details: Any = None, code: str = None):
        super().__init__(
            tag=ErrorTag.CONFIG,
            message=message,
            details=details,
            code=code or "CONFIG_ERROR",
        )


class ErrorHandler:
    """错误处理器"""
    
    def __init__(self):
        self._handlers: Dict[ErrorTag, Callable] = {}
        self._fallback: Optional[Callable] = None
        self._error_log: List[TaggedError] = []
    
    def register(self, tag: ErrorTag, handler: Callable):
        """注册错误处理器"""
        self._handlers[tag] = handler
    
    def set_fallback(self, handler: Callable):
        """设置回退处理器"""
        self._fallback = handler
    
    def handle(self, error: TaggedError) -> Any:
        """处理错误"""
        self._error_log.append(error)
        
        # 限制日志大小
        if len(self._error_log) > 1000:
            self._error_log = self._error_log[-500:]
        
        handler = self._handlers.get(error.tag)
        if handler:
            return handler(error)
        elif self._fallback:
            return self._fallback(error)
        else:
            self._default_handler(error)
    
    def _default_handler(self, error: TaggedError):
        """默认处理器"""
        print(f"Error: {error}")
        if error.cause:
            print(f"Cause: {error.cause}")
            traceback.print_exception(type(error.cause), error.cause, error.cause.__traceback__)
    
    def get_error_log(self, tag: Optional[ErrorTag] = None) -> List[TaggedError]:
        """获取错误日志"""
        if tag:
            return [e for e in self._error_log if e.tag == tag]
        return self._error_log
    
    def clear_log(self):
        """清除错误日志"""
        self._error_log.clear()


def handle_exception(func: Callable) -> Callable:
    """异常处理装饰器"""
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except TaggedError as e:
            return get_error_handler().handle(e)
        except Exception as e:
            error = InternalError(
                message=str(e),
                cause=e,
            )
            return get_error_handler().handle(error)
    return wrapper


# 全局实例
_error_handler: Optional[ErrorHandler] = None


def get_error_handler() -> ErrorHandler:
    """获取错误处理器单例"""
    global _error_handler
    if _error_handler is None:
        _error_handler = ErrorHandler()
    return _error_handler
