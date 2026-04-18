"""
学生相关 Schema
"""

from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field, EmailStr


class StudentProfileCreate(BaseModel):
    """创建学生简历"""
    student_id: str = Field(..., description='学生唯一ID')
    name: Optional[str] = Field(None, description='姓名')
    phone: Optional[str] = Field(None, description='电话')
    email: Optional[EmailStr] = Field(None, description='邮箱')
    degree: Optional[str] = Field(None, description='最高学历')
    school: Optional[str] = Field(None, description='毕业院校')
    major: Optional[str] = Field(None, description='专业')
    graduation_year: Optional[int] = Field(None, description='毕业年份')
    profile_json: Optional[Dict[str, Any]] = Field(None, description='完整简历JSON')


class StudentProfileUpdate(BaseModel):
    """更新学生简历"""
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[EmailStr] = None
    degree: Optional[str] = None
    school: Optional[str] = None
    major: Optional[str] = None
    graduation_year: Optional[int] = None


class StudentProfileResponse(BaseModel):
    """学生简历响应"""
    student_id: str
    name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    degree: Optional[str] = None
    school: Optional[str] = None
    major: Optional[str] = None
    graduation_year: Optional[int] = None
    completeness: Optional[float] = None
    source_file: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

    class Config:
        from_attributes = True


class DimensionScore(BaseModel):
    """维度得分"""
    name: str
    score: float
    passed: Optional[bool] = None
    skill_coverage: Optional[float] = None
    confidence: Optional[float] = None


class StudentPortraitResponse(BaseModel):
    """学生四维画像响应"""
    student_id: str
    target_job_id: Optional[str] = None
    dimensions: Dict[str, DimensionScore]
    total_score: float
    level: Optional[str] = None
    completeness: float
    skill_coverage: Optional[float] = None
    skill_count: Optional[int] = None
    created_at: Optional[str] = None

    class Config:
        from_attributes = True
