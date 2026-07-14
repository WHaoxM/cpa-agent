"""
Schemas 层
Pydantic 模型定义 - 请求/响应验证
"""

from .student import (
    StudentProfileCreate, StudentProfileResponse,
    StudentPortraitResponse
)
from .job import (
    JobPostingCreate, JobPortraitResponse
)
from .match import (
    MatchRunRequest, MatchResultResponse,
    SkillGapResponse, CareerRecommendationResponse
)
from .report import (
    ReportGenerateRequest, ReportResponse
)
from .agent import (
    ChatRequest, ChatResponse,
    SessionCreateResponse, SessionHistoryResponse
)
from .common import PaginationParams, PaginatedResponse, BaseResponse

__all__ = [
    'StudentProfileCreate',
    'StudentProfileResponse',
    'StudentPortraitResponse',
    'JobPostingCreate',
    'JobPortraitResponse',
    'MatchRunRequest',
    'MatchResultResponse',
    'SkillGapResponse',
    'CareerRecommendationResponse',
    'ReportGenerateRequest',
    'ReportResponse',
    'ChatRequest',
    'ChatResponse',
    'SessionCreateResponse',
    'SessionHistoryResponse',
    'PaginationParams',
    'PaginatedResponse',
    'BaseResponse',
]
