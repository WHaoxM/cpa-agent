"""
业务验证器层
包含各种业务规则验证函数
"""

from typing import Optional, List, Tuple
import re


def validate_student_id(student_id: str) -> Tuple[bool, Optional[str]]:
    """验证学生ID格式"""
    if not student_id:
        return False, "学生ID不能为空"
    if len(student_id) > 64:
        return False, "学生ID长度不能超过64"
    if not re.match(r'^[a-zA-Z0-9_]+$', student_id):
        return False, "学生ID只能包含字母、数字和下划线"
    return True, None


def validate_email(email: str) -> Tuple[bool, Optional[str]]:
    """验证邮箱格式"""
    if not email:
        return True, None
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if not re.match(pattern, email):
        return False, "邮箱格式不正确"
    return True, None


def validate_phone(phone: str) -> Tuple[bool, Optional[str]]:
    """验证手机号格式"""
    if not phone:
        return True, None
    pattern = r'^1[3-9]\d{9}$'
    if not re.match(pattern, phone):
        return False, "手机号格式不正确"
    return True, None


def validate_graduation_year(year: int) -> Tuple[bool, Optional[str]]:
    """验证毕业年份"""
    if year < 1990 or year > 2050:
        return False, "毕业年份应在1990-2050之间"
    return True, None


def validate_degree(degree: str) -> Tuple[bool, Optional[str]]:
    """验证学历"""
    valid_degrees = ['高中', '大专', '本科', '硕士', '博士', 'MBA', '其他']
    if degree not in valid_degrees:
        return False, f"学历必须是以下之一: {', '.join(valid_degrees)}"
    return True, None


def validate_score(score: float, min_val: float = 0, max_val: float = 100) -> Tuple[bool, Optional[str]]:
    """验证分数范围"""
    if score < min_val or score > max_val:
        return False, f"分数应在{min_val}-{max_val}之间"
    return True, None


def validate_skill_coverage(coverage: float) -> Tuple[bool, Optional[str]]:
    """验证技能覆盖率"""
    if coverage < 0 or coverage > 1:
        return False, "技能覆盖率应在0-1之间"
    return True, None


def validate_competitiveness_level(level: str) -> Tuple[bool, Optional[str]]:
    """验证竞争力等级"""
    valid_levels = ['极强', '强', '中等', '偏弱', '弱']
    if level not in valid_levels:
        return False, f"竞争力等级必须是以下之一: {', '.join(valid_levels)}"
    return True, None


def validate_match_level(level: str) -> Tuple[bool, Optional[str]]:
    """验证匹配等级"""
    valid_levels = ['极佳', '良好', '一般', '不匹配']
    if level not in valid_levels:
        return False, f"匹配等级必须是以下之一: {', '.join(valid_levels)}"
    return True, None


def validate_session_id(session_id: str) -> Tuple[bool, Optional[str]]:
    """验证会话ID格式"""
    if not session_id:
        return False, "会话ID不能为空"
    if len(session_id) > 64:
        return False, "会话ID长度不能超过64"
    if not session_id.startswith('session_'):
        return False, "会话ID必须以session_开头"
    return True, None


def validate_task_id(task_id: str) -> Tuple[bool, Optional[str]]:
    """验证任务ID格式"""
    if not task_id:
        return False, "任务ID不能为空"
    if len(task_id) > 64:
        return False, "任务ID长度不能超过64"
    if not task_id.startswith('task_'):
        return False, "任务ID必须以task_开头"
    return True, None


def validate_pipeline_name(pipeline: str) -> Tuple[bool, Optional[str]]:
    """验证管线名称"""
    valid_pipelines = [
        'L0_parse_resume',
        'L1_build_graph',
        'L2_generate_portrait',
        'L3_run_match',
        'L4_generate_report'
    ]
    if pipeline not in valid_pipelines:
        return False, f"管线必须是以下之一: {', '.join(valid_pipelines)}"
    return True, None


def validate_portrait_id(portrait_id: str) -> Tuple[bool, Optional[str]]:
    """验证画像ID格式"""
    if not portrait_id:
        return False, "画像ID不能为空"
    if len(portrait_id) > 64:
        return False, "画像ID长度不能超过64"
    if not portrait_id.startswith('portrait_'):
        return False, "画像ID必须以portrait_开头"
    return True, None


def validate_report_id(report_id: str) -> Tuple[bool, Optional[str]]:
    """验证报告ID格式"""
    if not report_id:
        return False, "报告ID不能为空"
    if len(report_id) > 64:
        return False, "报告ID长度不能超过64"
    if not report_id.startswith('report_'):
        return False, "报告ID必须以report_开头"
    return True, None


class StudentValidator:
    """学生数据验证器"""

    @staticmethod
    def validate_profile(student_id: str, email: Optional[str] = None,
                        phone: Optional[str] = None, graduation_year: Optional[int] = None,
                        degree: Optional[str] = None) -> Tuple[bool, List[str]]:
        """验证学生简历数据"""
        errors = []

        ok, msg = validate_student_id(student_id)
        if not ok:
            errors.append(msg)

        if email:
            ok, msg = validate_email(email)
            if not ok:
                errors.append(msg)

        if phone:
            ok, msg = validate_phone(phone)
            if not ok:
                errors.append(msg)

        if graduation_year:
            ok, msg = validate_graduation_year(graduation_year)
            if not ok:
                errors.append(msg)

        if degree:
            ok, msg = validate_degree(degree)
            if not ok:
                errors.append(msg)

        return len(errors) == 0, errors


class MatchValidator:
    """匹配数据验证器"""

    @staticmethod
    def validate_match_request(student_id: str, job_portrait_id: str) -> Tuple[bool, List[str]]:
        """验证匹配请求"""
        errors = []

        ok, msg = validate_student_id(student_id)
        if not ok:
            errors.append(f"学生ID: {msg}")

        ok, msg = validate_portrait_id(job_portrait_id)
        if not ok:
            errors.append(f"岗位画像ID: {msg}")

        return len(errors) == 0, errors

    @staticmethod
    def validate_score_range(d1: float, d2: float, d3: float, d4: float,
                           total: float) -> Tuple[bool, List[str]]:
        """验证得分范围"""
        errors = []

        for name, score in [('D1', d1), ('D2', d2), ('D3', d3), ('D4', d4)]:
            ok, msg = validate_score(score)
            if not ok:
                errors.append(f"{name}分数: {msg}")

        ok, msg = validate_score(total, 0, 100)
        if not ok:
            errors.append(f"总分: {msg}")

        return len(errors) == 0, errors
