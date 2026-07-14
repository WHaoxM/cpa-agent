"""
API 模块共享 helper

消除 backend/app/api/ 下各文件重复的:
1. 错误响应构造 (safe_error)
2. JSON 字段解析 (parse_json_field)
3. D1-D4 维度字典构造 (build_dimensions_d1_d4)
4. 七维子维度字典构造 (build_sub_dimensions)

设计原则: 只提供纯函数, 不引入新依赖, 不改变现有响应结构。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from ..exceptions import ErrorCode

logger = logging.getLogger("cpa-agent.api.helpers")


def safe_error(
    context: str,
    error: Optional[Exception] = None,
    *,
    message: Optional[str] = None,
    code: ErrorCode = ErrorCode.INTERNAL_ERROR,
) -> Dict[str, Any]:
    """
    统一错误响应: 日志记录详情, 客户端只返回通用提示

    Args:
        context: 操作描述, 如 "获取匹配结果" (会拼成 "获取匹配结果失败")
        error: 捕获的异常对象 (用于日志记录详情)
        message: 自定义客户端错误文案 (默认 "{context}失败，请稍后重试")
        code: 错误码 (默认 INTERNAL_ERROR)

    Returns:
        dict, 可直接传给 jsonify
    """
    detail = f": {error}" if error else ""
    logger.error(f"{context}失败{detail}")
    return {
        "success": False,
        "error": message or f"{context}失败，请稍后重试",
        "code": code.value,
    }


def parse_json_field(value: Any, default: Any = None) -> Any:
    """
    解析可能为 JSON 字符串的字段

    用于 MySQL 返回的 JSON 列 (会被某些 driver 序列化为字符串)。
    若已是 dict/list 则原样返回; 解析失败返回 default。

    Args:
        value: 待解析的值 (可能是 str/dict/list/None)
        default: 解析失败时的默认值 (默认 None)

    Returns:
        解析后的 dict/list, 或 default
    """
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return default
    return value if value is not None else default


def build_dimensions_d1_d4(
    d1_score: float,
    d2_score: float,
    d3_score: float,
    d4_score: float,
) -> Dict[str, Dict[str, Any]]:
    """
    构造 D1-D4 维度响应字典

    消除 match.py / profile.py 中重复的维度字典字面量。
    description 文案统一为通用版本 (原两处文案略有差异, 统一后更易维护)。

    Args:
        d1_score: D1 基础要求得分
        d2_score: D2 职业技能得分
        d3_score: D3 职业素养得分
        d4_score: D4 发展潜力得分

    Returns:
        {
            "D1": {"name": "基础要求", "score": ..., "description": ...},
            "D2": {...}, "D3": {...}, "D4": {...}
        }
    """
    return {
        "D1": {
            "name": "基础要求",
            "score": d1_score,
            "description": "学历、学校、专业等基础条件",
        },
        "D2": {
            "name": "职业技能",
            "score": d2_score,
            "description": "技术技能匹配程度",
        },
        "D3": {
            "name": "职业素养",
            "score": d3_score,
            "description": "沟通、团队、领导力等软技能",
        },
        "D4": {
            "name": "发展潜力",
            "score": d4_score,
            "description": "学习能力、成长空间、潜力评估",
        },
    }


def build_sub_dimensions(
    skill: float,
    cert: float,
    innovation: float,
    learning: float,
    stress: float,
    communication: float,
    internship: float,
) -> Dict[str, Dict[str, Any]]:
    """
    构造七维子维度响应字典 (D2 + D5-D10)

    对应前端 sub_dimensions 字段, 消除 match.py / profile.py 中的重复。

    Args:
        skill: 专业技能得分 (对应 D2)
        cert: 证书资质得分 (D5)
        innovation: 创新能力得分 (D6)
        learning: 学习能力得分 (D7)
        stress: 抗压能力得分 (D8)
        communication: 沟通能力得分 (D9)
        internship: 实习经验得分 (D10)

    Returns:
        {"skill": {"name": ..., "score": ...}, "cert": {...}, ...}
    """
    return {
        "skill":         {"name": "专业技能", "score": skill},
        "cert":          {"name": "证书资质", "score": cert},
        "innovation":    {"name": "创新能力", "score": innovation},
        "learning":      {"name": "学习能力", "score": learning},
        "stress":        {"name": "抗压能力", "score": stress},
        "communication": {"name": "沟通能力", "score": communication},
        "internship":    {"name": "实习经验", "score": internship},
    }
