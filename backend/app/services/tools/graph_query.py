"""Scoped graph tool: natural language -> validated template -> parameters.

The model may select a template and extract an entity name, but it never supplies
executable Cypher. Shared Skill and JobPortrait nodes lack public provenance.
"""

import json
from typing import Any, Dict, Optional, Tuple

from config.prompt_loader import load_prompt

from .base import BaseTool
from ...services.db import Neo4jClient, get_neo4j_client
from ...utils.llm_client import get_llm_client
from ...utils.logger import get_logger
from ..public_skill_graph import query_public_skills
from ..personal_graph import query_personal_skills
from ..personal_entity_links import query_reviewed_entity_links

logger = get_logger("cpa-agent.tool.graph_query")

# Only CareerNode has a currently provable seed/runtime public source. Never
# return whole nodes: the same Neo4j database also holds personal resume data.
PUBLIC_QUERIES: Dict[str, Tuple[str, Tuple[str, ...], bool]] = {
    "roles": (
        "MATCH (c:CareerNode) WHERE NOT c:Person AND labels(c) = ['CareerNode'] "
        "RETURN c.position_name AS role, c.level AS level LIMIT 50",
        ("role", "level"), False,
    ),
    "career_path": (
        "MATCH path=(start:CareerNode {position_name: $entity_name})-"
        "[:PROMOTION|TRANSFER*1..3]->(target:CareerNode) "
        "WHERE NOT start:Person AND NOT target:Person "
        "AND all(n IN nodes(path) WHERE labels(n) = ['CareerNode']) "
        "RETURN start.position_name AS start, target.position_name AS target, "
        "length(path) AS steps LIMIT 50",
        ("start", "target", "steps"), True,
    ),
}

DISABLED_UNSCOPED_SKILL_TYPES = frozenset({
    "skill_unavailable", "skill_roles", "role_skills",
})
PUBLIC_SKILL_TYPES = frozenset({"skills", "skill_neighbors", "skill_categories"})
PERSONAL_TYPES = frozenset({"personal_skills", "personal_entity_links"})

LEGACY_QUERY_TYPES = {
    "path": "career_path",
    "neighbors": "skill_neighbors",
    "statistics": "skill_categories",
}


class GraphQueryTool(BaseTool):
    """Public graph and Principal-bound personal lookup, never Text-to-Cypher."""

    @staticmethod
    def _failure(message, status="unavailable"):
        return json.dumps({"success": False, "status": status, "message": message}, ensure_ascii=False)

    def __init__(self, neo4j_client: Optional[Neo4jClient] = None, llm_client=None):
        self._neo4j = neo4j_client
        self._llm = llm_client

    @property
    def neo4j(self) -> Neo4jClient:
        if self._neo4j is None:
            self._neo4j = get_neo4j_client()
        return self._neo4j

    @property
    def llm(self):
        if self._llm is None:
            self._llm = get_llm_client()
        return self._llm

    @property
    def name(self) -> str:
        return "graph_query"

    @property
    def description(self) -> str:
        return (
            "查询公共 CareerNode 职业路径与隔离的 PublicSkill 审核种子；personal_skills 查询当前登录学生已核验的个人技能快照；"
            "personal_entity_links 查询当前学生人工接受且已完成私有图投影的等价实体链接。"
            "仅用户明确要求个人图谱视图或图谱关系时，用相应 personal 类型、offset=0；"
            "该快照按技能名排序，是已确认资料的派生投影，不代表简历原顺序。"
            "query 是用户自然语言问题；可选 query_type="
            "roles/career_path/skills/skill_neighbors/skill_categories/personal_skills/personal_entity_links，entity_name 是精确岗位或技能名。"
            "个人查询不接受用户 ID，缺可信快照时 unavailable；offset 从 0 开始，按 continuation 继续读。"
            "公共技能种子未发布时明确返回不可用；技能-岗位关系仍暂停。"
            "未提供类型时工具从 query 选择固定模板；不能提交 Cypher。"
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "用户的自然语言图谱问题"},
                "query_type": {"type": "string", "description": "固定模板：个人图谱技能 personal_skills；人工审阅的私有等价实体关系 personal_entity_links；普通已确认简历技能请用 career_data_read；公共图用 roles/career_path/skills/skill_neighbors/skill_categories"},
                "entity_name": {"type": "string", "description": "可选精确技能或岗位名；career_path 必填"},
                "offset": {"type": "integer", "default": 0, "description": "personal_skills/personal_entity_links 的分页起点；首读为 0，后续按 continuation 的 next_offset"},
            },
            "required": ["query"],
        }

    @staticmethod
    def _parse_selection(raw: str) -> Optional[Tuple[str, str]]:
        text = str(raw or "").strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        try:
            data = json.loads(text.strip())
        except (TypeError, ValueError):
            return None
        if not isinstance(data, dict) or set(data) - {"template", "entity_name"}:
            return None
        template = data.get("template")
        entity = data.get("entity_name")
        if not isinstance(template, str) or (
            template not in PUBLIC_QUERIES
            and template not in PUBLIC_SKILL_TYPES
            and template not in DISABLED_UNSCOPED_SKILL_TYPES
            and template not in PERSONAL_TYPES
        ):
            return None
        if entity is not None and not isinstance(entity, str):
            return None
        return template, (entity or "").strip()

    def _resolve_selection(
        self, query: str, query_type: str, entity_name: str,
    ) -> Optional[Tuple[str, str]]:
        template = LEGACY_QUERY_TYPES.get(query_type, query_type)
        if template in DISABLED_UNSCOPED_SKILL_TYPES:
            return template, entity_name
        if template in PUBLIC_SKILL_TYPES:
            return template, entity_name
        if template in PERSONAL_TYPES:
            return template, entity_name
        if query_type and template not in PUBLIC_QUERIES:
            return None
        if template:
            _cypher, _columns, needs_entity = PUBLIC_QUERIES[template]
            if not needs_entity or entity_name:
                return template, entity_name

        try:
            system = load_prompt(
                "tool.graph_query.system",
                schema="可查 CareerNode 职业路径与 PublicSkill 种子；personal_skills 仅查服务端当前学生的已核验私有快照。旧 Skill/JobPortrait/Person 不可查。",
                query_type=template or "自动判断",
            )
            raw = self.llm.chat(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": query},
                ],
                temperature=0.0,
                max_tokens=120,
            )
            selected = self._parse_selection(raw)
            if selected and template and selected[0] != template:
                return None
            return selected
        except Exception as exc:
            logger.warning("公共图谱模板分类失败: %s", exc)
            return None

    @staticmethod
    def _result(rows, columns, *, source_id="public_career_graph", coverage="公共职业图"):
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            return GraphQueryTool._failure("[graph_query] 执行失败：公共图谱结果格式异常", "failed")
        if any(value is not None and not isinstance(value, (str, int, float, bool))
               for row in rows for key in columns for value in [row.get(key)]):
            return GraphQueryTool._failure("[graph_query] 执行失败：公共字段投影异常", "failed")
        truncated = len(rows) > 10 or any(len(json.dumps(row.get(key), ensure_ascii=False)) > 160
                                        for row in rows[:10] for key in columns)
        return json.dumps({"success": True, "status": "partial" if truncated else "completed",
                           "row_count": len(rows), "displayed_count": min(10, len(rows)),
                           "truncated": truncated, "source_id": source_id,
                           "message": GraphQueryTool._format_rows(rows, columns) + "\n来源: " + source_id + "；" + coverage,
                           "continuation": "请缩小岗位或技能查询范围后重试" if truncated else None}, ensure_ascii=False)

    @staticmethod
    def _format_rows(rows: Any, columns: Tuple[str, ...]) -> str:
        if not isinstance(rows, list):
            return "[graph_query] 执行失败：公共图谱结果格式异常"
        lines = [f"查询结果 ({len(rows)} 条)"]
        if not rows:
            lines.append("未找到匹配的数据")
            return "\n".join(lines)
        for index, row in enumerate(rows[:10], 1):
            if not isinstance(row, dict):
                return "[graph_query] 执行失败：公共图谱结果格式异常"
            fields = []
            for key in columns:
                value = row.get(key)
                if value is not None and not isinstance(value, (str, int, float, bool)):
                    return "[graph_query] 执行失败：公共字段投影异常"
                rendered = json.dumps(value, ensure_ascii=False)
                if len(rendered) > 160:
                    rendered = rendered[:160] + "…[字段截断]"
                fields.append(f"{key}={rendered}")
            lines.append(f"  {index}. {', '.join(fields)}")
        if len(rows) > 10:
            lines.append(f"  ... 还有 {len(rows) - 10} 条记录未展示")
        return "\n".join(lines)

    def execute(self, **kwargs) -> str:
        query = str(kwargs.get("query") or "").strip()
        query_type = str(kwargs.get("query_type") or "").strip()
        entity_name = str(kwargs.get("entity_name") or "").strip()
        if not query:
            return self._failure("[graph_query] 错误: query 参数不能为空", "failed")
        if len(query) > 500 or len(entity_name) > 80:
            return self._failure("[graph_query] 错误: 查询或实体名过长，请缩短后重试", "failed")

        selection = self._resolve_selection(query, query_type, entity_name)
        if selection is None:
            return self._failure(
                "[graph_query] 无法确定图谱查询模板。本人已确认技能可指定 personal_skills；"
                "公共职业路径可指定 roles 或 career_path。请补充查询目标。"
            )
        template, entity_name = selection
        if len(entity_name) > 80:
            return self._failure("[graph_query] 错误: 实体名过长，请缩短后重试", "failed")
        if template in PERSONAL_TYPES:
            if template == "personal_entity_links":
                return self._personal_entity_links(kwargs.get("offset"))
            return self._personal_skills(entity_name, kwargs.get("offset"))
        if template in PUBLIC_SKILL_TYPES:
            try:
                result = query_public_skills(
                    template, {"entity_name": entity_name} if template == "skill_neighbors" else {},
                    client=self.neo4j,
                )
                if not result["success"]:
                    return self._failure("[graph_query] 不可用: " + result["message"])
                columns = ("category", "count") if template == "skill_categories" else ("name", "category", "relation", "source_id")
                return self._result(result["rows"], columns, source_id=result["source_id"], coverage=result["coverage"])
            except ValueError:
                return self._failure("[graph_query] 错误: 请提供 1–80 字的具体技能名", "failed")
            except Exception:
                logger.exception("公共技能查询失败")
                return self._failure("[graph_query] 执行失败，请稍后重试", "failed")
        if template in DISABLED_UNSCOPED_SKILL_TYPES:
            return self._failure(
                "[graph_query] 技能图谱查询暂不可用：共享 Skill 节点混有简历/JD 写入，"
                "缺少公共来源标记。可改问公共职业路径；不能据此判断没有相关岗位或技能。"
            )
        cypher, columns, needs_entity = PUBLIC_QUERIES[template]
        if needs_entity and not entity_name:
            return self._failure("[graph_query] 请提供具体技能或岗位名后重试；不能据此判断图谱中无数据", "needs_input")
        if len(entity_name) > 80:
            return self._failure("[graph_query] 错误: 实体名过长，请缩短后重试", "failed")

        try:
            parameters = {"entity_name": entity_name} if needs_entity else {}
            rows = self.neo4j.execute_query(cypher, parameters, read_only=True)
            logger.info("公共图谱查询完成: template=%s rows=%d", template, len(rows))
            return self._result(rows, columns)
        except Exception as exc:
            logger.error("公共图谱查询失败: template=%s error=%s", template, exc)
            return self._failure("[graph_query] 执行失败，请稍后重试", "failed")

    def _personal_skills(self, entity_name, raw_offset):
        from flask import has_request_context
        from ...api._helpers import (
            ScopeError, has_trusted_agent_worker_scope, resolve_request_scope,
        )

        if not has_request_context() and not has_trusted_agent_worker_scope():
            return self._failure("[graph_query] 权限拒绝：缺少可信服务端身份", "denied")
        try:
            scope = resolve_request_scope()
        except ScopeError:
            return self._failure("[graph_query] 权限拒绝：缺少可信服务端身份", "denied")
        try:
            # The JSON Schema advertises integer offset; direct legacy callers
            # may still pass decimal strings. Reject floats, bools and commands.
            if raw_offset is None or raw_offset == "":
                offset = 0
            elif type(raw_offset) is int:
                offset = raw_offset
            elif isinstance(raw_offset, str) and raw_offset.isascii() and raw_offset.isdecimal():
                offset = int(raw_offset)
            else:
                raise ValueError("offset 必须是 0 至 10000 的整数")
            result = query_personal_skills(self.neo4j, owner_student_id=scope.student_id,
                                           entity_name=entity_name, offset=offset)
            if not result["success"]:
                return json.dumps(result, ensure_ascii=False)
            rows = result["rows"]
            # The shared model egress has a 4000-character bound. Reduce the
            # actual window BEFORE egress so it cannot hide continuation or
            # silently discard rows from this page.
            while True:
                output = json.loads(self._result(
                    rows, ("name", "category", "evidence", "source_ref"),
                    source_id=result["source_id"], coverage="当前登录学生的已核验个人技能快照；不含旧未归属图",
                ))
                if not output["success"] or len(output["message"]) <= 3200 or len(rows) <= 1:
                    break
                rows = rows[:-1]
            if not output["success"]:
                return json.dumps(output, ensure_ascii=False)
            output["scope"] = "personal"
            has_more = result["has_more"] or len(rows) < len(result["rows"])
            output["next_offset"] = offset + len(rows) if has_more else None
            if has_more:
                output.update(status="partial", truncated=True,
                              continuation=f"继续调用 personal_skills，保持 entity_name 不变，offset={output['next_offset']}；字段截断的完整依据通过 career_data_read 读取")
            elif output["truncated"]:
                output["continuation"] = "字段预览已截断；通过 career_data_read 读取已确认简历/画像的完整依据"
            return json.dumps(output, ensure_ascii=False)
        except ValueError as exc:
            return self._failure("[graph_query] " + str(exc), "failed")
        except Exception:
            logger.exception("个人图谱查询失败")
            return self._failure("[graph_query] 个人图谱执行失败，请稍后重试", "failed")

    def _personal_entity_links(self, raw_offset):
        from flask import has_request_context
        from ...api._helpers import (
            ScopeError, has_trusted_agent_worker_scope, resolve_request_scope,
        )

        if not has_request_context() and not has_trusted_agent_worker_scope():
            return self._failure("[graph_query] 权限拒绝：缺少可信服务端身份", "denied")
        try:
            scope = resolve_request_scope()
        except ScopeError:
            return self._failure("[graph_query] 权限拒绝：缺少可信服务端身份", "denied")
        try:
            if raw_offset is None or raw_offset == "":
                offset = 0
            elif type(raw_offset) is int:
                offset = raw_offset
            elif isinstance(raw_offset, str) and raw_offset.isascii() and raw_offset.isdecimal():
                offset = int(raw_offset)
            else:
                raise ValueError("offset 必须是 0 至 10000 的整数")
            if not 0 <= offset <= 10000:
                raise ValueError("offset 必须是 0 至 10000 的整数")
            result = query_reviewed_entity_links(self.neo4j, owner_student_id=scope.student_id)
            if not result["success"]:
                return json.dumps(result, ensure_ascii=False)
            all_rows = result["rows"]
            rows = all_rows[offset:offset + 10]
            while True:
                output = json.loads(self._result(
                    rows, ("mention_index", "mention_name", "canonical_name", "category", "evidence"),
                    source_id="reviewed_private_entity_links",
                    coverage="当前登录学生已确认简历、当前人工审阅 revision 和已投影的私有等价链接；不含 related/拒绝/暂缓",
                ))
                if not output["success"] or len(output["message"]) <= 3200 or len(rows) <= 1:
                    break
                rows = rows[:-1]
            if not output["success"]:
                return json.dumps(output, ensure_ascii=False)
            has_more = offset + len(rows) < len(all_rows)
            output.update(scope="personal", source_task_id=result["source_task_id"],
                          review_revision=result["review_revision"],
                          total_count=len(all_rows), next_offset=offset + len(rows) if has_more else None)
            if has_more:
                output.update(status="partial", truncated=True,
                              continuation=f"继续调用 personal_entity_links，offset={output['next_offset']}；字段预览截断时通过 career_data_read 读取审阅原文")
            return json.dumps(output, ensure_ascii=False)
        except ValueError as exc:
            return self._failure("[graph_query] " + str(exc), "failed")
        except Exception:
            logger.exception("个人实体图谱查询失败")
            return self._failure("[graph_query] 个人实体图谱执行失败，请稍后重试", "failed")
