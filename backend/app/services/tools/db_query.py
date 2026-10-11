"""
Tool 2: db_query — MySQL 结构化数据查询
查询画像、匹配结果、报告历史、对话记录等业务数据
"""

import json
import re
from typing import Dict, Any, List, Optional, Tuple

from .base import BaseTool
from ...services.db import get_mysql_client, MySQLClient
from ...utils.logger import get_logger

logger = get_logger('cpa-agent.tool.db_query')

_BIND_RE = re.compile(r":([a-zA-Z_][a-zA-Z0-9_]*)")


class DbQueryTool(BaseTool):
    """MySQL 业务数据查询工具"""

    @staticmethod
    def _failure(message, status="unavailable"):
        return json.dumps({"success": False, "status": status, "message": message}, ensure_ascii=False)

    ALLOWED_TABLES = frozenset({
        "student_profile",
        "student_portrait_4d",
        "job_posting",
        "job_portrait_4d",
        "match_result",
        "skill_gap",
        "career_path",
        "career_report",
        "conversation",
        "conversation_message",
        "pipeline_task",
    })

    QUERY_TEMPLATES = {
        "student_profile": {
            "get_by_id": "SELECT * FROM student_profile WHERE student_id = :student_id",
            "list": "SELECT * FROM student_profile WHERE student_id = :student_id LIMIT :limit",
        },
        "student_portrait_4d": {
            "list": "SELECT * FROM student_portrait_4d WHERE student_id = :student_id ORDER BY created_at DESC LIMIT :limit",
            "get_by_id": "SELECT * FROM student_portrait_4d WHERE student_id = :student_id",
        },
        "job_posting": {
            "list": "SELECT * FROM job_posting LIMIT :limit",
            "get_by_id": "SELECT * FROM job_posting WHERE job_id = :job_id",
        },
        "job_portrait_4d": {
            "list": "SELECT * FROM job_portrait_4d LIMIT :limit",
            "get_by_id": "SELECT * FROM job_portrait_4d WHERE portrait_id = :portrait_id",
            "search": (
                "SELECT * FROM job_portrait_4d "
                "WHERE CAST(portrait_id AS CHAR) LIKE :q OR position_name LIKE :q "
                "LIMIT :limit"
            ),
        },
        "match_result": {
            "get_by_student_job": "SELECT * FROM match_result WHERE student_id = :student_id AND job_portrait_id = :job_portrait_id",
            "list_by_student": "SELECT * FROM match_result WHERE student_id = :student_id ORDER BY created_at DESC LIMIT :limit",
        },
        "skill_gap": {
            "get_by_student_job": "SELECT * FROM skill_gap WHERE student_id = :student_id AND job_portrait_id = :job_portrait_id",
        },
        "career_path": {
            "get_by_student_job": "SELECT * FROM career_path WHERE student_id = :student_id AND job_portrait_id = :job_portrait_id",
        },
        "career_report": {
            "get_by_id": "SELECT * FROM career_report WHERE report_id = :report_id AND student_id = :student_id",
            "list_by_student": "SELECT * FROM career_report WHERE student_id = :student_id ORDER BY created_at DESC LIMIT :limit",
        },
        "conversation_message": {
            "list_by_session": "SELECT m.* FROM conversation_message m JOIN conversation c ON c.session_id = m.session_id WHERE m.session_id = :session_id AND c.student_id = :student_id ORDER BY m.created_at ASC LIMIT :limit",
        },
    }

    def __init__(self, mysql_client: Optional[MySQLClient] = None):
        self._mysql = mysql_client

    @property
    def mysql(self) -> MySQLClient:
        if self._mysql is None:
            self._mysql = get_mysql_client()
        return self._mysql

    @property
    def name(self) -> str:
        return "db_query"

    @property
    def description(self) -> str:
        return """\
【业务数据查询 — MySQL】
查询 CPA-Agent 的结构化业务数据, 包括用户信息、画像、匹配结果、报告等。

【数据表】
- student_profile: 学生基本信息 + 简历解析结果
- student_portrait_4d: 学生四维画像 (D1基础/D2技能/D3素养/D4潜力)
- job_posting: 岗位原始数据
- job_portrait_4d: 岗位四维需求画像
- match_result: 人岗匹配结果 (四维得分 + 总分 + 是否匹配)
- skill_gap: 技能差距分析
- career_path: 职业路径推荐
- career_report: 职业发展报告
- conversation_message: 对话消息

【使用场景】
- "我的匹配结果怎么样"
- "查看Java后端岗位画像"
- "我的D2技能得分多少"
- "历史对话中我问过什么"

【返回内容】
- 结构化查询结果 (JSON 格式的文本描述)"""

    @property
    def parameters(self) -> Dict[str, str]:
        return {
            "query": "自然语言查询需求",
            "table_hint": "目标表提示 (可选: student/job/match/report/conversation)",
            # Scope fields: runtime PreToolUse injects/overrides; model should not invent others' ids
            "student_id": "（运行时注入）当前会话学生 id，勿填写他人 id",
            "session_id": "（运行时注入）当前会话 id",
            "job_id": "岗位 id（可选）",
            "job_portrait_id": "岗位画像 id（可选）",
            "portrait_id": "画像 id（可选）",
            "report_id": "报告 id（可选）",
        }

    def _guess_table(self, query: str, table_hint: str = "") -> Optional[Tuple[str, str]]:
        query_lower = query.lower()
        hint_lower = table_hint.lower()

        if "学生" in query or "我的" in query or "student" in hint_lower:
            if "画像" in query or "portrait" in hint_lower or "四维" in query:
                return "student_portrait_4d", "get_by_id"
            return "student_profile", "get_by_id"

        if "岗位" in query or "job" in hint_lower:
            if "画像" in query or "portrait" in hint_lower or "四维" in query:
                # 无 portrait_id 时走 list，避免 get_by_id 缺绑定参数
                return "job_portrait_4d", "list"
            return "job_posting", "list"

        if "匹配" in query or "match" in hint_lower:
            # Prefer list_by_student: get_by_student_job needs job_portrait_id the model rarely has
            return "match_result", "list_by_student"

        if "技能" in query or "差距" in query or "gap" in hint_lower:
            # skill_gap only has get_by_student_job — caller must supply job_portrait_id
            return "skill_gap", "get_by_student_job"

        if "路径" in query or "path" in hint_lower:
            return "career_path", "get_by_student_job"

        if "报告" in query or "report" in hint_lower:
            return "career_report", "list_by_student"

        if "对话" in query or "历史" in query or "conversation" in hint_lower:
            return "conversation_message", "list_by_session"

        return None

    def _build_query(self, table: str, query_type: str, params: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        if table not in self.ALLOWED_TABLES:
            raise ValueError(f"非法表名: {table}")

        templates = self.QUERY_TEMPLATES.get(table)
        if not templates:
            raise ValueError("No owner-scoped query template")

        template = templates.get(query_type)
        if not template:
            raise ValueError("Unknown owner-scoped query template")

        return template, params

    # Never dump these into tool text (model + egress)
    def _format_results(self, results: List[Dict[str, Any]], table: str) -> str:
        if not results:
            return f"表中没有数据: {table}"

        from ..agent_runtime.field_policy import project_row

        parts = [f"查询结果 ({len(results)} 条):"]

        for i, row in enumerate(results[:5]):
            proj = project_row(row if isinstance(row, dict) else {}, for_model=True)
            bits = [f"{k}={v}" for k, v in proj.items() if v not in (None, "")]
            parts.append(f"  {i+1}. " + ", ".join(bits))

        if len(results) > 5:
            parts.append(f"  ... 还有 {len(results) - 5} 条")

        return "\n".join(parts)

    def execute(self, **kwargs) -> str:
        query = kwargs.get("query", "")
        table_hint = kwargs.get("table_hint", "")

        if not query:
            return self._failure("[db_query] 错误: query 参数不能为空", "failed")

        logger.info(f"db_query 执行: query={query}, table_hint={table_hint}")

        try:
            guessed = self._guess_table(query, table_hint)

            if not guessed:
                return self._failure(f"[db_query] 无法确定查询目标表。可用表: {', '.join(sorted(self.ALLOWED_TABLES))}", "needs_input")

            table, query_type = guessed

            # Both tables can be populated from runtime JD input. A table name or
            # a read-only SQL template is not public-source provenance.
            if table in {"job_posting", "job_portrait_4d"}:
                return self._failure("[db_query] 不可用: 岗位表缺少可验证的公共来源/归属标记，请使用已隔离的公共图谱。")

            from flask import has_request_context
            from ...api._helpers import resolve_request_scope, ScopeError

            if not has_request_context():
                return self._failure("[db_query] 权限拒绝: 缺少可信服务端 Principal；不会使用工具参数 student_id 代替认证。", "denied")
            try:
                scope = resolve_request_scope()
            except ScopeError:
                return self._failure("[db_query] 权限拒绝: 缺少可信服务端 Principal。", "denied")

            params = {}
            for key in ["student_id", "job_portrait_id", "portrait_id", "report_id", "session_id", "job_id"]:
                if key in kwargs and kwargs[key] not in (None, ""):
                    params[key] = kwargs[key]
            params.setdefault("limit", 5)
            params["student_id"] = scope.student_id

            sql, sql_params = self._build_query(table, query_type, params)
            needed = set(_BIND_RE.findall(sql))
            missing = needed - set(sql_params.keys())
            if missing:
                # Do NOT silently list unrelated rows (cross-tenant / PII leak).
                logger.warning(
                    "db_query 缺少绑定参数 %s; table=%s type=%s — 拒绝降级 list",
                    sorted(missing),
                    table,
                    query_type,
                )
                return self._failure(
                    f"[db_query] 缺少必要参数: {', '.join(sorted(missing))}。"
                    f"请提供正确的 student_id / 业务 id 后再查询，不会返回无关列表。"
                )

            logger.debug(f"执行的 SQL: {sql}, 参数: {sql_params}")

            results = self.mysql.execute(sql, parameters=sql_params)

            return self._format_results(results, table)

        except ValueError as e:
            logger.error(f"db_query 安全校验失败: {e}")
            return self._failure("[db_query] 安全校验失败", "denied")
        except Exception as e:
            logger.error(f"db_query 执行失败: {e}")
            return self._failure("[db_query] 执行失败，请稍后重试", "failed")
