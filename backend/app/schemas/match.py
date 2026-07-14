"""
匹配相关 Schema
"""

from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field

from .student import DimensionScore


class MatchRunRequest(BaseModel):
    """执行匹配请求"""
    student_id: str = Field(..., description='学生ID')
    job_portrait_id: str = Field(..., description='岗位画像ID')


class MatchResultResponse(BaseModel):
    """匹配结果响应"""
    student_id: str
    job_portrait_id: str
    dimensions: Dict[str, DimensionScore]
    total_score: float
    is_matched: bool
    match_level: Optional[str] = None
    skill_coverage: Optional[float] = None
    created_at: Optional[str] = None

    class Config:
        from_attributes = True


class SkillGapItem(BaseModel):
    """技能差距项"""
    skill_name: str
    gap_type: str  # critical / quick_learn / bonus
    importance: float
    learning_difficulty: Optional[str] = None
    recommended_resource: Optional[str] = None


class SkillGapResponse(BaseModel):
    """技能差距响应"""
    student_id: str
    job_portrait_id: str
    critical_count: int
    quick_learn_count: int
    total_gap_count: int
    gaps: List[SkillGapItem]
    created_at: Optional[str] = None

    class Config:
        from_attributes = True


class CareerPathStep(BaseModel):
    """职业路径步骤"""
    step: int
    position_name: str
    level: str
    years_to_promotion: Optional[int] = None
    skills_needed: List[str]


class CareerRecommendationResponse(BaseModel):
    """职业推荐响应"""
    student_id: str
    current_level: Optional[str] = None
    recommended_dir: Optional[str] = None
    promotion_steps: int
    lateral_count: int
    paths: List[CareerPathStep]
    created_at: Optional[str] = None

    class Config:
        from_attributes = True
