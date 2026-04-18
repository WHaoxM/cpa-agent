"""
通用 Schema 定义
"""

from typing import Generic, TypeVar, Optional, Any, Dict, List
from pydantic import BaseModel, Field

T = TypeVar('T')


class BaseResponse(BaseModel, Generic[T]):
    """统一响应格式"""
    success: bool = True
    message: str = "success"
    data: Optional[T] = None


class PaginationParams(BaseModel):
    """分页参数"""
    page: int = Field(default=1, ge=1, description='页码')
    page_size: int = Field(default=20, ge=1, le=100, description='每页数量')


class PaginatedResponse(BaseModel, Generic[T]):
    """分页响应"""
    items: List[T]
    total: int
    page: int
    page_size: int
    total_pages: int


class ErrorDetail(BaseModel):
    """错误详情"""
    code: str
    message: str
    details: Optional[Dict[str, Any]] = None


class HealthCheck(BaseModel):
    """健康检查响应"""
    status: str = "ok"
    service: str = "CPA-Agent Backend"
    version: Optional[str] = None


class MetricsData(BaseModel):
    """性能指标"""
    status: str
    cpu_percent: float
    memory_percent: float
    memory_used_mb: float
    memory_available_mb: float
