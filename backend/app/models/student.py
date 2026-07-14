"""
学生相关数据模型
"""

from datetime import datetime
from typing import Optional, Dict, Any
from sqlalchemy import Column, String, Text, JSON, Integer, Float, DateTime, SmallInteger, Index, UniqueConstraint, ForeignKey
from sqlalchemy.orm import relationship

from .base import Base


class StudentProfile(Base):
    """学生简历表 (L0 输出)"""
    __tablename__ = 'student_profile'
    __table_args__ = (
        Index('idx_name', 'name'),
        Index('idx_degree', 'degree'),
        Index('idx_school', 'school'),
        Index('idx_major', 'major'),
        Index('idx_graduation', 'graduation_year'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    student_id = Column(String(64), unique=True, nullable=False, comment='学生唯一ID')
    name = Column(String(100), comment='姓名')
    phone = Column(String(20), comment='电话 (脱敏存储)')
    email = Column(String(100), comment='邮箱')

    degree = Column(String(20), comment='最高学历: 本科/硕士/博士')
    school = Column(String(100), comment='毕业院校')
    major = Column(String(100), comment='专业')
    graduation_year = Column(SmallInteger, comment='毕业年份')

    profile_json = Column(JSON, nullable=False, comment='完整 student_profile.json')

    data_quality = Column(JSON, comment='L0 解析质量指标')
    completeness = Column(Float, comment='字段填充率 0-1')

    source_file = Column(String(255), comment='原始简历文件名')
    source_type = Column(String(20), default='pdf', comment='pdf/docx/txt/json')
    parse_version = Column(String(20), default='v1', comment='解析管线版本')

    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

    portraits = relationship('StudentPortrait4D', back_populates='student')
    match_results = relationship('MatchResult', back_populates='student')

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'student_id': self.student_id,
            'name': self.name,
            'phone': self.phone,
            'email': self.email,
            'degree': self.degree,
            'school': self.school,
            'major': self.major,
            'graduation_year': self.graduation_year,
            'profile_json': self.profile_json,
            'data_quality': self.data_quality,
            'completeness': self.completeness,
            'source_file': self.source_file,
            'source_type': self.source_type,
            'parse_version': self.parse_version,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class StudentPortrait4D(Base):
    """学生四维画像表 (L2 输出)"""
    __tablename__ = 'student_portrait_4d'
    __table_args__ = (
        UniqueConstraint('student_id', 'target_job_id', name='uk_student_job'),
        Index('idx_competitiveness', 'competitiveness'),
        Index('idx_level', 'competitiveness_lvl'),
        Index('idx_skill_coverage', 'skill_coverage'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    student_id = Column(String(64), ForeignKey('student_profile.student_id'), nullable=False, comment='→ student_profile.student_id')
    target_job_id = Column(String(64), comment='目标岗位ID')

    d1_score = Column(Float, nullable=False, default=0, comment='D1 基础要求得分 0-100')
    d2_score = Column(Float, nullable=False, default=0, comment='D2 职业技能得分 0-100')
    d3_score = Column(Float, nullable=False, default=0, comment='D3 职业素养得分 0-100')
    d4_score = Column(Float, nullable=False, default=0, comment='D4 发展潜力得分 0-100')

    competitiveness = Column(Float, nullable=False, default=0, comment='综合得分')
    competitiveness_lvl = Column(String(10), comment='极强/强/中等/偏弱/弱')
    completeness = Column(Float, nullable=False, default=0, comment='字段填充率 0-100')

    skill_coverage = Column(Float, comment='技能覆盖率 0-1 (R-03 ≥0.8)')
    skill_count = Column(Integer, nullable=False, default=0, comment='技能总数')

    d3_confidence = Column(Float, comment='D3 Shannon 熵映射 0-1')

    portrait_json = Column(JSON, nullable=False, comment='完整 student_portrait_4d.json')

    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

    student = relationship('StudentProfile', back_populates='portraits')

    def to_dict(self) -> Dict[str, Any]:
        return {
            'student_id': self.student_id,
            'target_job_id': self.target_job_id,
            'dimensions': {
                'D1': {'score': self.d1_score, 'name': '基础要求'},
                'D2': {'score': self.d2_score, 'name': '职业技能', 'skill_coverage': self.skill_coverage},
                'D3': {'score': self.d3_score, 'name': '职业素养', 'confidence': self.d3_confidence},
                'D4': {'score': self.d4_score, 'name': '发展潜力'},
            },
            'total_score': self.competitiveness,
            'level': self.competitiveness_lvl,
            'completeness': self.completeness,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
