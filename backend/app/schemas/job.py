"""
岗位相关 Schema
"""

from typing import Optional, Dict, Any
from pydantic import BaseModel, Field


class JobPostingCreate(BaseModel):
    """创建岗位"""
    job_id: str = Field(..., description='岗位唯一ID')
    position_name: str = Field(..., description='岗位名称')
    position_normalized: Optional[str] = Field(None, description='标准化岗位名')
    job_category: Optional[str] = Field(None, description='岗位大类')
    company_name: Optional[str] = Field(None, description='公司名')
    company_industry: Optional[str] = Field(None, description='行业')
    company_size: Optional[str] = Field(None, description='规模')
    degree_requirement: Optional[str] = Field(None, description='学历要求')
    experience_min: Optional[int] = Field(None, description='最低经验(年)')
    salary_min: Optional[int] = Field(None, description='最低薪资')
    salary_max: Optional[int] = Field(None, description='最高薪资')
    posting_json: Optional[Dict[str, Any]] = Field(None, description='完整JD JSON')


class JobPostingResponse(BaseModel):
    """岗位响应"""
    job_id: str
    position_name: str
    position_normalized: Optional[str] = None
    job_category: Optional[str] = None
    company_name: Optional[str] = None
    company_industry: Optional[str] = None
    company_size: Optional[str] = None
    degree_requirement: Optional[str] = None
    experience_min: Optional[int] = None
    salary_min: Optional[int] = None
    salary_max: Optional[int] = None
    is_it_position: bool = True

    class Config:
        from_attributes = True


class JobPortraitResponse(BaseModel):
    """岗位画像响应"""
    portrait_id: str
    position_name: str
    jd_count: int
    required_degree: Optional[str] = None
    major_strict: bool = False
    required_skill_cnt: int = 0
    weights: Dict[str, float]
    created_at: Optional[str] = None

    class Config:
        from_attributes = True
