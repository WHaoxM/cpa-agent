"""
统一错误处理模块
定义错误码和统一响应格式
"""

from enum import Enum
from typing import Optional, Any, Dict


class ErrorCode(str, Enum):
    """错误码枚举"""

    PARAM_INVALID = "PARAM_INVALID"
    NOT_FOUND = "NOT_FOUND"
    DB_ERROR = "DB_ERROR"
    NEO4J_ERROR = "NEO4J_ERROR"
    MYSQL_ERROR = "MYSQL_ERROR"
    REDIS_ERROR = "REDIS_ERROR"
    MILVUS_ERROR = "MILVUS_ERROR"
    LLM_ERROR = "LLM_ERROR"
    TOOL_ERROR = "TOOL_ERROR"
    PIPELINE_ERROR = "PIPELINE_ERROR"
    AUTH_ERROR = "AUTH_ERROR"
    PERMISSION_ERROR = "PERMISSION_ERROR"
    RATE_LIMIT_ERROR = "RATE_LIMIT_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"


ERROR_MESSAGES = {
    ErrorCode.PARAM_INVALID: "参数无效",
    ErrorCode.NOT_FOUND: "资源不存在",
    ErrorCode.DB_ERROR: "数据库错误",
    ErrorCode.NEO4J_ERROR: "Neo4j 图数据库错误",
    ErrorCode.MYSQL_ERROR: "MySQL 数据库错误",
    ErrorCode.REDIS_ERROR: "Redis 缓存错误",
    ErrorCode.MILVUS_ERROR: "Milvus 向量数据库错误",
    ErrorCode.LLM_ERROR: "LLM 服务调用失败",
    ErrorCode.TOOL_ERROR: "工具执行失败",
    ErrorCode.PIPELINE_ERROR: "管线执行失败",
    ErrorCode.AUTH_ERROR: "认证失败",
    ErrorCode.PERMISSION_ERROR: "权限不足",
    ErrorCode.RATE_LIMIT_ERROR: "请求过于频繁",
    ErrorCode.INTERNAL_ERROR: "服务器内部错误",
    ErrorCode.SERVICE_UNAVAILABLE: "服务不可用",
}


class APIException(Exception):
    """API 统一异常类"""

    def __init__(
        self,
        error_code: ErrorCode,
        message: Optional[str] = None,
        details: Optional[Any] = None,
        status_code: int = 400,
    ):
        self.error_code = error_code
        self.message = message or ERROR_MESSAGES.get(error_code, "未知错误")
        self.details = details
        self.status_code = status_code
        super().__init__(self.message)

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "success": False,
            "error": self.message,
            "code": self.error_code.value,
        }
        if self.details:
            result["details"] = self.details
        return result


def success_response(data: Any = None, message: str = "success") -> Dict[str, Any]:
    """成功响应"""
    result = {
        "success": True,
        "message": message,
    }
    if data is not None:
        result["data"] = data
    return result


def error_response(
    error_code: ErrorCode,
    message: Optional[str] = None,
    details: Optional[Any] = None,
    status_code: int = 400,
) -> Dict[str, Any]:
    """错误响应"""
    return APIException(
        error_code=error_code,
        message=message,
        details=details,
        status_code=status_code,
    ).to_dict()


def not_found(resource: str, resource_id: str) -> APIException:
    """资源不存在异常"""
    return APIException(
        error_code=ErrorCode.NOT_FOUND,
        message=f"{resource} 不存在: {resource_id}",
        status_code=404,
    )


def invalid_param(param_name: str, reason: str = "") -> APIException:
    """参数无效异常"""
    message = f"参数 {param_name} 无效"
    if reason:
        message += f": {reason}"
    return APIException(
        error_code=ErrorCode.PARAM_INVALID,
        message=message,
        status_code=400,
    )


def db_error(operation: str, original_error: Exception) -> APIException:
    """数据库错误异常"""
    return APIException(
        error_code=ErrorCode.DB_ERROR,
        message=f"数据库操作失败: {operation}",
        details=str(original_error),
        status_code=500,
    )


def service_unavailable(service: str) -> APIException:
    """服务不可用异常"""
    return APIException(
        error_code=ErrorCode.SERVICE_UNAVAILABLE,
        message=f"{service} 服务不可用",
        status_code=503,
    )
