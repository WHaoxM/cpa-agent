"""
管理员端业务数据模型
补齐 admin.py / admin_kb.py 中已引用但未 ORM 化的表
"""

from datetime import datetime
from typing import Dict, Any
from sqlalchemy import Column, String, Text, JSON, Integer, DateTime, Index

from .base import Base


class KnowledgeBase(Base):
    """知识库上传记录表"""
    __tablename__ = 'knowledge_base'
    __table_args__ = (
        Index('idx_status', 'status'),
        Index('idx_type', 'type'),
        Index('idx_created', 'created_at'),
    )

    id = Column(String(64), primary_key=True, comment='如 kb_xxxxxxxxxxxx')
    title = Column(String(200), nullable=False, comment='标题')
    type = Column(String(30), nullable=False, default='text', comment='text/skill/jd/file')
    status = Column(String(20), nullable=False, default='pending', comment='pending/processing/completed/failed')
    file_path = Column(String(500), comment='上传文件路径')
    file_size = Column(Integer, comment='字节')
    content_sha256 = Column(String(64), comment='私有来源文件 SHA-256')
    content_text = Column(Text, comment='原始内容文本')
    parsed_entities = Column(JSON, comment='解析出的实体 (skills/roles/relations)')
    intent_json = Column(JSON, comment='意图识别结果')
    uploaded_by = Column(String(64), comment='→ user_account.id')
    error_message = Column(Text)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'title': self.title,
            'type': self.type,
            'status': self.status,
            'file_path': self.file_path,
            'file_size': self.file_size,
            'content_sha256': self.content_sha256,
            'parsed_entities': self.parsed_entities,
            'intent': self.intent_json,
            'uploaded_by': self.uploaded_by,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class JobDataset(Base):
    """岗位数据集上传记录表"""
    __tablename__ = 'job_dataset'
    __table_args__ = (
        Index('idx_status', 'status'),
        Index('idx_category', 'category'),
        Index('idx_created', 'created_at'),
    )

    id = Column(String(64), primary_key=True, comment='如 ds_xxxxxxxxxxxx')
    title = Column(String(200), nullable=False)
    count = Column(Integer, nullable=False, default=0, comment='岗位数量')
    category = Column(String(50), comment='数据集分类')
    status = Column(String(20), nullable=False, default='pending', comment='pending/processing/completed/failed')
    file_path = Column(String(500))
    file_size = Column(Integer)
    content_sha256 = Column(String(64), comment='私有来源文件 SHA-256')
    staged_record_count = Column(Integer, nullable=False, default=0, comment='服务端解析的暂存 JSON 条目数，非权威岗位数量')
    description = Column(Text)
    uploaded_by = Column(String(64), comment='→ user_account.id')
    error_message = Column(Text)
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'title': self.title,
            'count': self.count,
            'staged_record_count': self.staged_record_count,
            'category': self.category,
            'status': self.status,
            'file_path': self.file_path,
            'file_size': self.file_size,
            'content_sha256': self.content_sha256,
            'description': self.description,
            'uploaded_by': self.uploaded_by,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class OovEntity(Base):
    """OOV 实体审核队列表"""
    __tablename__ = 'oov_entity'
    __table_args__ = (
        Index('idx_status', 'status'),
        Index('idx_type', 'entity_type'),
        Index('idx_source', 'source'),
        Index('idx_created', 'created_at'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    oov_id = Column(String(64), unique=True, nullable=False, comment='如 oov_xxxxxxxxxxxx')
    raw_entity = Column(String(200), nullable=False, comment='原始未识别实体')
    entity_type = Column(String(32), nullable=False, comment='skill/position/major/certificate/degree/award')
    context_json = Column(JSON, comment='上下文 (出现位置/句子)')
    source = Column(String(64), comment='来源: 简历ID/JD ID/知识库ID')
    frequency = Column(Integer, nullable=False, default=1, comment='出现频次')
    status = Column(String(20), nullable=False, default='pending', comment='pending/approved/rejected')
    canonical_mapping = Column(String(200), comment='→ canonical_entity.canonical_name (审核后)')
    reviewed_by = Column(String(64), comment='→ user_account.id')
    reviewed_at = Column(DateTime)
    review_notes = Column(Text, comment='审核备注 (拒绝原因等)')
    created_at = Column(DateTime, default=datetime.now, nullable=False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'oov_id': self.oov_id,
            'raw_entity': self.raw_entity,
            'entity_type': self.entity_type,
            'context': self.context_json or {},
            'source': self.source,
            'frequency': self.frequency,
            'status': self.status,
            'canonical_mapping': self.canonical_mapping,
            'reviewed_by': self.reviewed_by,
            'reviewed_at': self.reviewed_at.isoformat() if self.reviewed_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
