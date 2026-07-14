"""
数据模型层 (Models)
SQLAlchemy ORM 模型定义
"""

from .student import StudentProfile, StudentPortrait4D
from .job import JobPosting, JobPortrait4D
from .match import MatchResult, SkillGap, CareerPath
from .report import CareerReport
from .conversation import Conversation, ConversationMessage
from .task import PipelineTask
from .account import UserAccount, ClassInfo, StudentGrade, ActiveUserLog, AdminAuditLog
from .admin_data import KnowledgeBase, JobDataset, OovEntity

__all__ = [
    'StudentProfile',
    'StudentPortrait4D',
    'JobPosting',
    'JobPortrait4D',
    'MatchResult',
    'SkillGap',
    'CareerPath',
    'CareerReport',
    'Conversation',
    'ConversationMessage',
    'PipelineTask',
    # 账号与权限
    'UserAccount',
    'ClassInfo',
    'StudentGrade',
    'ActiveUserLog',
    'AdminAuditLog',
    # 管理员端业务
    'KnowledgeBase',
    'JobDataset',
    'OovEntity',
]
