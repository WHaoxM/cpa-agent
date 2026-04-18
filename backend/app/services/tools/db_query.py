"""
Tool 2: db_query — MySQL 结构化数据查询
查询画像、匹配结果、报告历史、对话记录等业务数据
"""

import json
from typing import Dict, Any, List, Optional

from .base import BaseTool
from ...services.db import get_mysql_client, MySQLClient
from ...utils.logger import get_logger

logger = get_logger('cpa-agent.tool.db_query')


class DbQueryTool(BaseTool):
    """MySQL 业务数据查询工具"""

    QUERY_TEMPLATES = {
        "student_profile": {
            "get_by_id": "SELECT * FROM student_profile WHERE student_id = {student_id}",
            "list": "SELECT * FROM student_profile LIMIT {limit}",
        },
        "student_portrait_4d": {
            "get_by_id": "SELECT * FROM student_portrait_4d WHERE student_id = {student_id}",
        },
        "job_portrait": {
            "list": "SELECT * FROM job_portrait LIMIT {limit}",
            "get_by_id": "SELECT * FROM job_portrait WHERE portrait_id = {portrait_id}",
        },
        "job_portrait_4d": {
            "get_by_id": "SELECT * FROM job_portrait_4d WHERE portrait_id = {portrait_id}",
        },
        "match_result": {
            "get_by_student_job": "SELECT * FROM match_result WHERE student_id = {student_id} AND job_id = {job_id}",
            "list_by_student": "SELECT * FROM match_result WHERE student_id = {student_id} ORDER BY created_at DESC LIMIT {limit}",
        },
        "skill_gap": {
            "get_by_student_job": "SELECT * FROM skill_gap WHERE student_id = {student_id} AND job_id = {job_id}",
        },
        "career_report": {
            "get_by_id": "SELECT * FROM career_report WHERE report_id = {report_id}",
            "list_by_student": "SELECT * FROM career_report WHERE student_id = {student_id} ORDER BY created_at DESC LIMIT {limit}",
        },
        "conversation_history": {
            "list_by_session": "SELECT * FROM conversation_history WHERE session_id = {session_id} ORDER BY created_at ASC",
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
- career_report: 职业发展报告
- conversation_history: 对话历史

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
            "table_hint": "目标表提示 (可选: student/job/match/report/conversation)"
        }

    def _guess_table(self, query: str, table_hint: str = "") -> Optional[tuple]:
        """根据查询内容和提示猜测目标表和查询类型"""
        query_lower = query.lower()
        hint_lower = table_hint.lower()

        if "学生" in query or "我的" in query or "student" in hint_lower:
            if "画像" in query or "portrait" in hint_lower or "四维" in query:
                return "student_portrait_4d", "get_by_id"
            return "student_profile", "get_by_id"

        if "岗位" in query or "job" in hint_lower:
            if "画像" in query or "portrait" in hint_lower:
                return "job_portrait_4d", "get_by_id"
            return "job_portrait", "get_by_id"

        if "匹配" in query or "match" in hint_lower:
            return "match_result", "get_by_student_job"

        if "技能" in query or "差距" in query or "gap" in hint_lower:
            return "skill_gap", "get_by_student_job"

        if "报告" in query or "report" in hint_lower:
            return "career_report", "list_by_student"

        if "对话" in query or "历史" in query or "conversation" in hint_lower:
            return "conversation_history", "list_by_session"

        return None

    def _build_query(self, table: str, query_type: str, params: Dict[str, Any]) -> str:
        """构建 SQL 查询"""
        template_key = f"{table}.{query_type}"
        template = self.QUERY_TEMPLATES.get(template_key)

        if not template:
            return f"SELECT * FROM {table} LIMIT 10"

        sql = template
        for key, value in params.items():
            if isinstance(value, str):
                sql = sql.replace(f"{{{key}}}", f"'{value}'")
            else:
                sql = sql.replace(f"{{{key}}}", str(value))

        return sql

    def _format_results(self, results: List[Dict[str, Any]], table: str) -> str:
        """格式化查询结果"""
        if not results:
            return f"表中没有数据: {table}"

        parts = [f"查询结果 ({len(results)} 条):"]

        for i, row in enumerate(results[:5]):
            row_str = ", ".join(f"{k}={v}" for k, v in row.items() if v is not None)
            parts.append(f"  {i+1}. {row_str}")

        if len(results) > 5:
            parts.append(f"  ... 还有 {len(results) - 5} 条")

        return "\n".join(parts)

    def execute(self, **kwargs) -> str:
        query = kwargs.get("query", "")
        table_hint = kwargs.get("table_hint", "")

        if not query:
            return "[db_query] 错误: query 参数不能为空"

        logger.info(f"db_query 执行: query={query}, table_hint={table_hint}")

        try:
            guessed = self._guess_table(query, table_hint)

            if not guessed:
                tables = self.mysql.get_tables()
                return f"[db_query] 无法确定查询目标表。可用表: {', '.join(tables[:10])}"

            table, query_type = guessed

            params = {}
            for key in ["student_id", "job_id", "portrait_id", "report_id", "session_id"]:
                if key in kwargs:
                    params[key] = kwargs[key]

            if not params:
                params["limit"] = 5

            sql = self._build_query(table, query_type, params)
            logger.debug(f"执行的 SQL: {sql}")

            results = self.mysql.execute(sql)

            return self._format_results(results, table)

        except Exception as e:
            logger.error(f"db_query 执行失败: {e}")
            return f"[db_query] 执行失败: {str(e)}"
