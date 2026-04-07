"""
Tool 2: db_query — MySQL 结构化数据查询
查询画像、匹配结果、报告历史、对话记录等业务数据
"""

from typing import Dict

from .base import BaseTool
from ...utils.logger import get_logger

logger = get_logger('cpa-agent.tool.db_query')


class DbQueryTool(BaseTool):
    """MySQL 业务数据查询工具"""

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

    def execute(self, **kwargs) -> str:
        query = kwargs.get("query", "")
        table_hint = kwargs.get("table_hint", "")

        logger.info(f"db_query 执行: query={query}, table_hint={table_hint}")

        # TODO: 实现 Text-to-SQL 或预定义查询模板
        # 1. 根据 query + table_hint 选择查询模板
        # 2. 填充参数 (student_id / job_id 等从 session 上下文获取)
        # 3. 执行 MySQL 查询
        # 4. 格式化结果为文本

        return f"[db_query] 查询: {query}\n暂未实现, 待接入 MySQL 连接"
