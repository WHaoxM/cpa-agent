"""
账号与权限相关数据模型
对齐前端 types/user.ts: User / ClassData / StudentGrade / SystemStats
"""

from datetime import datetime
from typing import Dict, Any, Optional
from sqlalchemy import Column, String, Text, JSON, Integer, BigInteger, Float, DateTime, Index

from .base import Base


class UserAccount(Base):
    """统一账号表 (学生/教师/管理员)"""
    __tablename__ = 'user_account'
    __table_args__ = (
        Index('idx_role', 'role'),
        Index('idx_status', 'status'),
        Index('idx_class', 'class_id'),
        Index('idx_created', 'created_at'),
    )

    id = Column(String(64), primary_key=True, comment='用户唯一ID (学生场景下与 student_profile.student_id 对齐)')
    username = Column(String(50), unique=True, nullable=False, comment='登录用户名')
    password_hash = Column(String(255), nullable=False, comment='Argon2id 哈希（新账号）')
    name = Column(String(100), comment='姓名')
    email = Column(String(100), comment='邮箱')
    phone = Column(String(20), comment='电话 (脱敏存储)')
    avatar = Column(String(500), comment='头像URL')
    role = Column(String(20), nullable=False, default='student', comment='student/teacher/admin')
    status = Column(String(20), nullable=False, default='active', comment='active/disabled')
    auth_epoch = Column(BigInteger, nullable=False, default=0, comment='登录会话失效代数')
    profile_revision = Column(BigInteger, nullable=False, default=0, server_default='0',
                              comment='个人资料并发版本')
    class_id = Column(String(64), comment='→ class_info.id (学生所属班级)')
    signature = Column(String(255), comment='个性签名')
    last_login_at = Column(DateTime, comment='最近登录时间')
    last_login_ip = Column(String(45), comment='最近登录IP')
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'username': self.username,
            'name': self.name,
            'email': self.email,
            'avatar': self.avatar,
            'role': self.role,
            'status': self.status,
            'classId': self.class_id,
            'phone': self.phone,
            'signature': self.signature,
            'createdAt': self.created_at.isoformat() if self.created_at else None,
        }


class ClassInfo(Base):
    """班级信息表"""
    __tablename__ = 'class_info'
    __table_args__ = (
        Index('idx_teacher', 'teacher_id'),
        Index('idx_name', 'name'),
    )

    id = Column(String(64), primary_key=True)
    name = Column(String(100), nullable=False, comment='班级名称')
    teacher_id = Column(String(64), comment='→ user_account.id (班主任)')
    course_ids = Column(JSON, comment='关联课程ID列表')
    student_count = Column(Integer, nullable=False, default=0, comment='学生数 (冗余字段, 定期同步)')
    description = Column(String(255))
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'name': self.name,
            'studentCount': self.student_count,
            'courseIds': self.course_ids or [],
            'teacherId': self.teacher_id,
            'description': self.description,
        }


class StudentGrade(Base):
    """学生成绩表"""
    __tablename__ = 'student_grade'
    __table_args__ = (
        Index('idx_course', 'course_id'),
        Index('idx_score', 'total_score'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(64), nullable=False, comment='→ user_account.id')
    course_id = Column(String(64), nullable=False, comment='→ course.course_id')
    total_score = Column(Float, nullable=False, default=0, comment='总分 0-100')
    quiz_scores = Column(JSON, comment='各次小测验分数 [88, 92, 75]')
    completion_rate = Column(Float, nullable=False, default=0, comment='完成率 0-1')
    graded_at = Column(DateTime, comment='评分时间')
    created_at = Column(DateTime, default=datetime.now, nullable=False)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, nullable=False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'userId': self.user_id,
            'courseId': self.course_id,
            'totalScore': self.total_score,
            'quizScores': self.quiz_scores or [],
            'completionRate': self.completion_rate,
            'gradedAt': self.graded_at.isoformat() if self.graded_at else None,
        }


class ActiveUserLog(Base):
    """用户登录日志表 (DAU/WAU/MAU 统计来源)"""
    __tablename__ = 'active_user_log'
    __table_args__ = (
        Index('idx_user', 'user_id'),
        Index('idx_login', 'login_at'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(64), nullable=False, comment='→ user_account.id')
    login_at = Column(DateTime, default=datetime.now, nullable=False)
    ip = Column(String(45))
    user_agent = Column(String(255))

    def to_dict(self) -> Dict[str, Any]:
        return {
            'user_id': self.user_id,
            'login_at': self.login_at.isoformat() if self.login_at else None,
            'ip': self.ip,
        }


class AdminAuditLog(Base):
    """管理员操作审计日志表"""
    __tablename__ = 'admin_audit_log'
    __table_args__ = (
        Index('idx_admin', 'admin_id'),
        Index('idx_action', 'action'),
        Index('idx_target', 'target_type', 'target_id'),
        Index('idx_created', 'created_at'),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    admin_id = Column(String(64), nullable=False, comment='→ user_account.id')
    action = Column(String(50), nullable=False, comment='create/update/delete/upload/approve/reject/login')
    target_type = Column(String(50), nullable=False, comment='knowledge_base/dataset/oov/user/config')
    target_id = Column(String(64))
    detail_json = Column(JSON, comment='操作详情')
    ip = Column(String(45))
    created_at = Column(DateTime, default=datetime.now, nullable=False)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'admin_id': self.admin_id,
            'action': self.action,
            'target_type': self.target_type,
            'target_id': self.target_id,
            'detail': self.detail_json,
            'ip': self.ip,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
