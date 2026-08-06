"""
API 模块共享 helper

消除 backend/app/api/ 下各文件重复的:
1. 错误响应构造 (safe_error)
2. JSON 字段解析 (parse_json_field)
3. D1-D4 维度字典构造 (build_dimensions_d1_d4)
4. 七维子维度字典构造 (build_sub_dimensions)
5. 分页参数边界保护 (clamp_pagination)
6. 路由 try/except 样板消除 (api_handler 装饰器)

设计原则: 只提供纯函数, 不引入新依赖, 不改变现有响应结构。
"""

from __future__ import annotations

import functools
import json
import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple

from flask import g, jsonify, request

from ..exceptions import ErrorCode

logger = logging.getLogger("cpa-agent.api.helpers")


@dataclass(frozen=True)
class Principal:
    """Authenticated runtime identity resolved by the server."""

    principal_id: str
    student_id: str
    source: str = "demo"


@dataclass(frozen=True)
class RequestScope:
    """Trusted request scope used for user-owned resources."""

    principal: Principal
    session_id: Optional[str] = None

    @property
    def principal_id(self) -> str:
        return self.principal.principal_id

    @property
    def student_id(self) -> str:
        return self.principal.student_id


class ScopeError(Exception):
    """Auth/owner error that should be returned as 401/403, not 500."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 403,
        code: ErrorCode = ErrorCode.PERMISSION_ERROR,
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code


def _configured_demo_principal() -> Principal:
    from ..config import Config

    student_id = str(getattr(Config, "DEMO_STUDENT_ID", "student_001") or "student_001")
    principal_id = str(
        getattr(Config, "DEMO_PRINCIPAL_ID", "") or f"demo:{student_id}"
    )
    return Principal(principal_id=principal_id, student_id=student_id, source="demo")


def _principal_from_auth_adapter() -> Optional[Principal]:
    """Read identity supplied by future auth middleware; never trust client body/query."""
    raw = getattr(g, "cpa_principal", None) or getattr(g, "principal", None)
    if isinstance(raw, Principal):
        return raw
    if isinstance(raw, dict):
        sid = raw.get("student_id") or raw.get("studentId") or raw.get("user_id")
        pid = raw.get("principal_id") or raw.get("sub") or raw.get("id") or sid
        if sid and pid:
            return Principal(str(pid), str(sid), source="auth_adapter")

    sid = getattr(g, "student_id", None) or getattr(g, "user_id", None)
    pid = getattr(g, "principal_id", None) or sid
    if sid and pid:
        return Principal(str(pid), str(sid), source="auth_adapter")
    return None


def resolve_request_scope(*, session_id: Optional[str] = None) -> RequestScope:
    """
    Resolve the trusted identity for this request.

    Demo mode intentionally ignores client-supplied student/user ids and binds all
    user resources to a configured demo principal. Strict mode fails closed until
    real auth middleware populates flask.g with a principal.
    """
    from ..config import Config

    if getattr(Config, "DEMO_MODE", True):
        principal = _configured_demo_principal()
    else:
        principal = _principal_from_auth_adapter()
        if principal is None:
            raise ScopeError(
                "未认证：生产/strict 模式需要服务端认证适配器提供 Principal",
                status_code=401,
                code=ErrorCode.AUTH_ERROR,
            )
    return RequestScope(principal=principal, session_id=session_id)


def scope_error_response(error: ScopeError):
    return jsonify(
        {
            "success": False,
            "error": error.message,
            "code": error.code.value,
        }
    ), error.status_code


def safe_forbidden(message: str) -> Dict[str, Any]:
    return {"success": False, "error": message, "code": ErrorCode.PERMISSION_ERROR.value}


def require_owner(
    owner_id: Optional[str],
    scope: RequestScope,
    *,
    resource: str = "资源",
) -> None:
    if not owner_id:
        raise ScopeError(f"{resource} 缺少归属信息，已拒绝访问")
    if str(owner_id) != str(scope.student_id):
        raise ScopeError(f"无权访问该{resource}")


def require_student_path(student_id: str, scope: RequestScope) -> None:
    if str(student_id) != str(scope.student_id):
        raise ScopeError("路径 student_id 与当前 Principal 不匹配")


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


def safe_bad_request(message: str) -> Dict[str, Any]:
    """构造 400 参数错误响应体 (统一带 code 字段)

    用法: return jsonify(safe_bad_request("source_id 必填")), 400
    """
    return {"success": False, "error": message, "code": ErrorCode.PARAM_INVALID.value}


def safe_not_found(message: str) -> Dict[str, Any]:
    """构造 404 资源不存在响应体 (统一带 code 字段)

    用法: return jsonify(safe_not_found("任务不存在")), 404
    """
    return {"success": False, "error": message, "code": ErrorCode.NOT_FOUND.value}


def _iso(dt: Any) -> Optional[str]:
    """datetime → ISO 字符串, None 安全

    用法: _iso(task["created_at"]) 替代 task["created_at"].isoformat() if task["created_at"] else None
    """
    return dt.isoformat() if dt else None


def api_handler(context: str) -> Callable:
    """路由装饰器: 统一 try/except + success 包装, 消除样板

    被装饰函数只需返回纯 data (任意可 JSON 序列化对象), 装饰器负责:
    1. 包装为 {"success": True, "data": ...}
    2. 捕获异常返回 {"success": False, "error": ..., "code": ...} + 500

    若函数内需要返回 400/404 等客户端错误, 直接 return jsonify(...), 400
    装饰器会检测 Response 对象原样返回, 不再包装。

    Args:
        context: 操作描述, 如 "获取知识库列表" (异常时拼成 "获取知识库列表失败")

    注意:
        - 需要返回非 {"success": True, "data": ...} 结构的路由(如带 meta/next_steps)
          不要用此装饰器, 保持显式写法
    """
    def decorator(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                data = fn(*args, **kwargs)
                # 函数内已返回 Response (如 400/404), 原样返回
                if hasattr(data, 'status_code'):
                    return data
                # return jsonify(...), 400  形式
                if (
                    isinstance(data, tuple)
                    and len(data) >= 1
                    and hasattr(data[0], 'status_code')
                ):
                    return data
                return jsonify({"success": True, "data": data})
            except Exception as e:
                return jsonify(safe_error(context, e)), 500
        return wrapper
    return decorator


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


def sub_dimensions_from_payload(
    skill_fallback: float,
    payload: Any = None,
) -> Dict[str, Dict[str, Any]]:
    """
    从 portrait_json / result_json 抽取七维子分；列上无 D5–D10 时的兼容入口。

    优先读 payload['sub_dimensions'] 或扁平字段；缺省用 skill_fallback / 0。
    """
    data = parse_json_field(payload) if payload is not None else None
    if not isinstance(data, dict):
        data = {}

    nested = data.get("sub_dimensions")
    if isinstance(nested, dict):
        def _score(key: str, default: float = 0.0) -> float:
            node = nested.get(key)
            if isinstance(node, dict):
                return float(node.get("score") or default)
            if node is None:
                return default
            try:
                return float(node)
            except (TypeError, ValueError):
                return default

        return build_sub_dimensions(
            _score("skill", float(skill_fallback or 0)),
            _score("cert"),
            _score("innovation"),
            _score("learning"),
            _score("stress"),
            _score("communication"),
            _score("internship"),
        )

    return build_sub_dimensions(
        float(data.get("d2_score") or data.get("d2_match") or skill_fallback or 0),
        float(data.get("d5_score") or data.get("d5_match") or 0),
        float(data.get("d6_score") or data.get("d6_match") or 0),
        float(data.get("d7_score") or data.get("d7_match") or 0),
        float(data.get("d8_score") or data.get("d8_match") or 0),
        float(data.get("d9_score") or data.get("d9_match") or 0),
        float(data.get("d10_score") or data.get("d10_match") or 0),
    )


# 分页参数边界常量
MAX_LIMIT = 100  # 单次查询最大返回条数, 防止客户端传入超大 limit 导致 DB 过载


def clamp_pagination(
    limit: Any,
    offset: Any,
    *,
    default_limit: int = 20,
) -> Tuple[int, int]:
    """对分页参数进行类型安全 + 边界保护

    处理 request.args.get('limit', default, type=int) 的结果:
    - None / 非正数 → default_limit
    - 超过 MAX_LIMIT → MAX_LIMIT
    - offset None / 负数 → 0

    Args:
        limit: 原始 limit 值 (可能为 None 或 int)
        offset: 原始 offset 值 (可能为 None 或 int)
        default_limit: 默认 limit (当 limit 为 None 或非法时使用)

    Returns:
        (safe_limit, safe_offset): 边界保护后的分页参数
    """
    safe_limit = default_limit
    if isinstance(limit, int) and limit > 0:
        safe_limit = min(limit, MAX_LIMIT)

    safe_offset = 0
    if isinstance(offset, int) and offset > 0:
        safe_offset = offset

    return safe_limit, safe_offset
