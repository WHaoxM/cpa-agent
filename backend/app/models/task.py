"""
管线任务数据模型
"""

from datetime import datetime
from typing import Dict, Any
from sqlalchemy import Column, String, Text, JSON, Integer, Float, DateTime

from .base import Base


class PipelineTask(Base):
    """管线任务表"""
    __tablename__ = 'pipeline_task'

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String(64), unique=True, nullable=False, comment='任务唯一ID')

    pipeline = Column(String(30), nullable=False, comment='L0_parse_resume/L1_build_graph/...')
    status = Column(String(20), default='pending', comment='pending/running/completed/failed')
    progress = Column(Integer, default=0, comment='进度 0-100')

    params_json = Column(JSON, comment='管线输入参数')

    result_json = Column(JSON, comment='管线输出摘要')
    error_message = Column(Text, comment='失败时的错误信息')

    student_id = Column(String(64), comment='关联学生ID')
    job_id = Column(String(64), comment='关联岗位ID')
    triggered_by = Column(String(20), default='api', comment='api/agent/cron')

    started_at = Column(DateTime, comment='开始时间')
    completed_at = Column(DateTime, comment='完成时间')
    created_at = Column(DateTime, default=datetime.now, nullable=False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'task_id': self.task_id,
            'pipeline': self.pipeline,
            'status': self.status,
            'progress': self.progress,
            'params': self.params_json,
            'result': self.result_json,
            'error_message': self.error_message,
            'student_id': self.student_id,
            'job_id': self.job_id,
            'triggered_by': self.triggered_by,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
