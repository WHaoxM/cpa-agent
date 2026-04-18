"""
会话相关数据模型
"""

from datetime import datetime
from typing import List, Dict, Any
from sqlalchemy import Column, String, Text, JSON, Integer, DateTime
from sqlalchemy.orm import relationship

from .base import Base


class Conversation(Base):
    """对话会话表"""
    __tablename__ = 'conversation'

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(64), unique=True, nullable=False, comment='会话唯一ID')
    student_id = Column(String(64), comment='关联学生ID')

    title = Column(String(200), comment='会话标题')
    status = Column(String(20), default='active', comment='active/archived/deleted')

    message_count = Column(Integer, default=0, comment='消息总数')
    tool_call_count = Column(Integer, default=0, comment='工具调用总数')

    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

    messages = relationship('ConversationMessage', back_populates='conversation', order_by='ConversationMessage.created_at')

    def to_dict(self) -> Dict[str, Any]:
        return {
            'session_id': self.session_id,
            'student_id': self.student_id,
            'title': self.title,
            'status': self.status,
            'message_count': self.message_count,
            'tool_call_count': self.tool_call_count,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


class ConversationMessage(Base):
    """对话消息表"""
    __tablename__ = 'conversation_message'

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(64), nullable=False, comment='→ conversation.session_id')
    message_id = Column(String(64), unique=True, nullable=False, comment='消息唯一ID')

    role = Column(String(20), nullable=False, comment='user/assistant/system')
    content = Column(Text, nullable=False, comment='消息内容')

    tool_calls_json = Column(JSON, comment='本次回复的工具调用记录')
    thinking_json = Column(JSON, comment='思考步骤')
    iterations = Column(Integer, comment='ReAct 迭代次数')
    elapsed_seconds = Column(Float, comment='响应耗时(秒)')

    created_at = Column(DateTime, default=datetime.now, nullable=False)

    conversation = relationship('Conversation', back_populates='messages')

    def to_dict(self) -> Dict[str, Any]:
        return {
            'message_id': self.message_id,
            'session_id': self.session_id,
            'role': self.role,
            'content': self.content,
            'tool_calls': self.tool_calls_json,
            'thinking': self.thinking_json,
            'iterations': self.iterations,
            'elapsed_seconds': self.elapsed_seconds,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
