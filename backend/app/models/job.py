"""
岗位相关数据模型
"""

from datetime import datetime
from typing import Optional, Dict, Any
from sqlalchemy import Column, String, Text, JSON, Integer, Float, DateTime, SmallInteger, Boolean

from .base import Base


class JobPosting(Base):
    """岗位信息表 (L0 输出)"""
    __tablename__ = 'job_posting'

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(String(64), unique=True, nullable=False, comment='岗位唯一ID')
    position_name = Column(String(200), nullable=False, comment='岗位名称 (原始)')
    position_normalized = Column(String(200), comment='标准化岗位名')
    job_category = Column(String(50), comment='岗位大类')

    company_name = Column(String(200), comment='公司名')
    company_industry = Column(String(100), comment='行业')
    company_size = Column(String(50), comment='规模')

    degree_requirement = Column(String(20), comment='学历要求')
    experience_min = Column(SmallInteger, comment='最低经验(年)')
    salary_min = Column(Integer, comment='最低薪资(元/月)')
    salary_max = Column(Integer, comment='最高薪资(元/月)')

    posting_json = Column(JSON, nullable=False, comment='完整 job_posting.json')

    is_it_position = Column(Boolean, default=True, comment='R-01: 是否IT岗位')

    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'job_id': self.job_id,
            'position_name': self.position_name,
            'position_normalized': self.position_normalized,
            'job_category': self.job_category,
            'company_name': self.company_name,
            'company_industry': self.company_industry,
            'company_size': self.company_size,
            'degree_requirement': self.degree_requirement,
            'experience_min': self.experience_min,
            'salary_min': self.salary_min,
            'salary_max': self.salary_max,
            'is_it_position': self.is_it_position,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class JobPortrait4D(Base):
    """岗位四维需求画像表 (L2 输出)"""
    __tablename__ = 'job_portrait_4d'

    id = Column(Integer, primary_key=True, autoincrement=True)
    portrait_id = Column(String(64), unique=True, nullable=False, comment='画像唯一ID')
    position_name = Column(String(200), nullable=False, comment='标准化岗位名')

    jd_count = Column(Integer, default=0, comment='聚合的 JD 数量 (R-05 ≥10)')

    required_degree = Column(String(20), comment='学历要求')
    major_strict = Column(Boolean, default=False, comment='是否严格限专业')
    required_skill_cnt = Column(Integer, default=0, comment='必备技能数')

    d1_weight = Column(Float, default=0.25, comment='D1 权重')
    d2_weight = Column(Float, default=0.35, comment='D2 权重')
    d3_weight = Column(Float, default=0.20, comment='D3 权重')
    d4_weight = Column(Float, default=0.20, comment='D4 权重')

    portrait_json = Column(JSON, nullable=False, comment='完整 job_portrait_4d.json')

    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'portrait_id': self.portrait_id,
            'position_name': self.position_name,
            'jd_count': self.jd_count,
            'required_degree': self.required_degree,
            'major_strict': self.major_strict,
            'required_skill_cnt': self.required_skill_cnt,
            'weights': {
                'D1': self.d1_weight,
                'D2': self.d2_weight,
                'D3': self.d3_weight,
                'D4': self.d4_weight,
            },
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
