"""
报告相关数据模型
"""

from datetime import datetime
from typing import Dict, Any
from sqlalchemy import Column, String, JSON, Integer, Float, DateTime

from .base import Base


class CareerReport(Base):
    """职业发展报告表 (L4 输出)"""
    __tablename__ = 'career_report'

    id = Column(Integer, primary_key=True, autoincrement=True)
    report_id = Column(String(64), unique=True, nullable=False, comment='报告唯一ID')
    student_id = Column(String(64), nullable=False, comment='→ student_profile.student_id')
    job_portrait_id = Column(String(64), nullable=False, comment='→ job_portrait_4d.portrait_id')

    title = Column(String(200), comment='报告标题')
    status = Column(String(20), default='pending', comment='pending/generating/completed/failed')

    ch1_json = Column(JSON, comment='Ch1 个人画像摘要')
    ch2_json = Column(JSON, comment='Ch2 目标岗位分析')
    ch3_json = Column(JSON, comment='Ch3 人岗匹配度分析')
    ch4_json = Column(JSON, comment='Ch4 职业发展路径')
    ch5_json = Column(JSON, comment='Ch5 差距与学习规划')
    ch6_json = Column(JSON, comment='Ch6 评估周期')

    report_json = Column(JSON, comment='完整 career_report.json')

    html_path = Column(String(500), comment='导出 HTML 路径')
    pdf_path = Column(String(500), comment='导出 PDF 路径')

    elapsed_seconds = Column(Float, comment='生成总耗时(秒)')

    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'report_id': self.report_id,
            'student_id': self.student_id,
            'job_portrait_id': self.job_portrait_id,
            'title': self.title,
            'status': self.status,
            'chapters': {
                'ch1': self.ch1_json,
                'ch2': self.ch2_json,
                'ch3': self.ch3_json,
                'ch4': self.ch4_json,
                'ch5': self.ch5_json,
                'ch6': self.ch6_json,
            },
            'html_path': self.html_path,
            'pdf_path': self.pdf_path,
            'elapsed_seconds': self.elapsed_seconds,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
