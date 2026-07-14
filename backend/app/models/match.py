"""
匹配相关数据模型
"""

from datetime import datetime
from typing import Dict, Any
from sqlalchemy import Column, String, JSON, Integer, Float, DateTime, Boolean, Index, UniqueConstraint, ForeignKey
from sqlalchemy.orm import relationship

from .base import Base


class MatchResult(Base):
    """匹配结果表 (L3 输出)"""
    __tablename__ = 'match_result'
    __table_args__ = (
        UniqueConstraint('student_id', 'job_portrait_id', name='uk_student_job'),
        Index('idx_total_score', 'total_score'),
        Index('idx_is_matched', 'is_matched'),
        Index('idx_student', 'student_id'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    student_id = Column(String(64), ForeignKey('student_profile.student_id'), nullable=False, comment='→ student_profile.student_id')
    job_portrait_id = Column(String(64), nullable=False, comment='→ job_portrait_4d.portrait_id')

    d1_match = Column(Float, nullable=False, default=0, comment='D1 匹配得分')
    d2_match = Column(Float, nullable=False, default=0, comment='D2 匹配得分')
    d3_match = Column(Float, nullable=False, default=0, comment='D3 匹配得分')
    d4_match = Column(Float, nullable=False, default=0, comment='D4 匹配得分')
    total_score = Column(Float, nullable=False, default=0, comment='加权总分')

    d1_pass = Column(Boolean, nullable=False, default=False, comment='D1 硬约束是否通过')
    skill_coverage = Column(Float, nullable=True, comment='D2 技能覆盖率')
    is_matched = Column(Boolean, nullable=False, default=False, comment='最终是否匹配')
    match_level = Column(String(20), comment='极佳/良好/一般/不匹配')

    result_json = Column(JSON, nullable=False, comment='完整 match_result.json')

    created_at = Column(DateTime, default=datetime.now, nullable=False)

    student = relationship('StudentProfile', back_populates='match_results')

    def to_dict(self) -> Dict[str, Any]:
        return {
            'student_id': self.student_id,
            'job_portrait_id': self.job_portrait_id,
            'dimensions': {
                'D1': {'score': self.d1_match, 'passed': self.d1_pass},
                'D2': {'score': self.d2_match, 'skill_coverage': self.skill_coverage},
                'D3': {'score': self.d3_match},
                'D4': {'score': self.d4_match},
            },
            'total_score': self.total_score,
            'is_matched': self.is_matched,
            'match_level': self.match_level,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class SkillGap(Base):
    """技能差距表 (L3 输出)"""
    __tablename__ = 'skill_gap'
    __table_args__ = (
        UniqueConstraint('student_id', 'job_portrait_id', name='uk_student_job'),
        Index('idx_student', 'student_id'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    student_id = Column(String(64), nullable=False, comment='→ student_profile.student_id')
    job_portrait_id = Column(String(64), nullable=False, comment='→ job_portrait_4d.portrait_id')

    critical_count = Column(Integer, nullable=False, default=0, comment='关键缺失技能数')
    quick_learn_count = Column(Integer, nullable=False, default=0, comment='快速可学技能数')
    total_gap_count = Column(Integer, nullable=False, default=0, comment='总差距技能数')

    gap_json = Column(JSON, nullable=False, comment='完整 skill_gap.json')

    created_at = Column(DateTime, default=datetime.now, nullable=False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'student_id': self.student_id,
            'job_portrait_id': self.job_portrait_id,
            'critical_count': self.critical_count,
            'quick_learn_count': self.quick_learn_count,
            'total_gap_count': self.total_gap_count,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class CareerPath(Base):
    """职业路径推荐表 (L3 输出)"""
    __tablename__ = 'career_path'
    __table_args__ = (
        UniqueConstraint('student_id', 'job_portrait_id', name='uk_student_job'),
        Index('idx_student', 'student_id'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    student_id = Column(String(64), nullable=False, comment='→ student_profile.student_id')
    job_portrait_id = Column(String(64), nullable=False, comment='→ job_portrait_4d.portrait_id')

    recommended_dir = Column(String(200), comment='推荐发展方向')
    promotion_steps = Column(Integer, nullable=False, default=0, comment='晋升路径步数')
    lateral_count = Column(Integer, nullable=False, default=0, comment='横向选项数')

    path_json = Column(JSON, nullable=False, comment='完整 career_path.json')

    created_at = Column(DateTime, default=datetime.now, nullable=False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'student_id': self.student_id,
            'job_portrait_id': self.job_portrait_id,
            'recommended_dir': self.recommended_dir,
            'promotion_steps': self.promotion_steps,
            'lateral_count': self.lateral_count,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
