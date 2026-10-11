"""Read current owner-scoped career resources through the existing tool gate."""

from __future__ import annotations

import json

from .base import BaseTool
from ..agent_runtime.resource_catalog import list_platform_resources, read_platform_resource
from ..db import get_mysql_client


class CareerDataReadTool(BaseTool):
    @property
    def name(self) -> str:
        return "career_data_read"

    @property
    def description(self) -> str:
        return (
            "读取当前登录学生在各功能已核验的业务资料。仅在不知道 URI 时 operation=list；"
            "本轮已核验的资源引用已给出 URI 时直接 operation=read，不要先 list 或重复读 overview。"
            "要按原顺序列出已确认简历技能前 10 项，用 operation=evidence、uri=career://me/resume、"
            "section=skills、max_items=10；返回来源版本、覆盖和下一页位置。这是权威简历顺序，不是图谱排序。"
            "简历和画像可读 career://me/overview、career://me/resume、career://me/portrait；"
            "简历section=entity_links并列提供模型对齐建议和最新人工审阅revision；"
            "rejected/deferred不能作为接受，accepted的related不是等价；分页revision改变必须重新读取。"
            "简历诊断报告从 career://me/resume-reports 发现，按当前确认版本读取章节；"
            "其中 chapter:role_directions 是有简历页证据的候选探索方向，"
            "job_catalog 是每次动态回读的已发布岗位目录；前者不是实际招聘职位或正式匹配分，"
            "目录为空也可读取方向。career://me/reports 是需可信岗位来源的专项报告。"
            "匹配、报告、行动计划等以目录返回的 URI 和 sections 为准。"
            "列表及详细依据按 offset/limit 分页；"
            "coverage.has_more=true 时必须继续读取，不可把一页当全文。"
            "status=partial 或 unavailable 不是正式成果，未知分数为 null。"
            "只读；不接受模型提供的 student_id 或浏览器摘要作为资料来源。"
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "operation": {"type": "string", "description": "list、read 或 evidence；列表前 10 项用 evidence，已知 URI 时不要先 list"},
                "uri": {"type": "string", "description": "先 list 获得当前可读的 career://me/... URI"},
                "section": {"type": "string", "description": "目录返回的 sections 之一，默认 overview"},
                "dimension": {"type": "string", "description": "读取画像 evidence 时指定 D1/D2/D3/D4"},
                "offset": {"type": "integer", "description": "分页起点，首次为 0"},
                "limit": {"type": "integer", "description": "每页 1 至 3 项，默认 3"},
                "max_items": {"type": "integer", "description": "仅 evidence：本轮最多读取的列表项，默认 10，上限 10"},
                "student_id": {"type": "string", "description": "由运行时注入的当前用户 ID，模型不要填写"},
            },
            "required": ["operation"],
        }

    def execute(self, **kwargs) -> str:
        from flask import has_request_context
        from ...api._helpers import ScopeError, resolve_request_scope

        if not has_request_context():
            return json.dumps({"success": False, "status": "denied", "message": "缺少可信服务端身份"}, ensure_ascii=False)
        try:
            scope = resolve_request_scope()
        except ScopeError:
            return json.dumps({"success": False, "status": "denied", "message": "缺少可信服务端身份"}, ensure_ascii=False)
        try:
            operation = kwargs.get("operation") or "list"
            mysql = get_mysql_client()
            if operation == "list":
                data = list_platform_resources(mysql, student_id=scope.student_id)
            elif operation == "read":
                data = read_platform_resource(
                    mysql, student_id=scope.student_id,
                    uri=kwargs.get("uri") or "",
                    section=kwargs.get("section") or "overview",
                    dimension=kwargs.get("dimension") or "",
                    offset=kwargs.get("offset") if kwargs.get("offset") is not None else 0,
                    limit=kwargs.get("limit") if kwargs.get("limit") is not None else 3,
                )
            elif operation == "evidence":
                from ..agent_runtime.evidence_retrieval import build_evidence_pack

                uri = kwargs.get("uri") or ""
                section = kwargs.get("section") or "overview"
                max_items = kwargs.get("max_items") if kwargs.get("max_items") is not None else 10
                if type(max_items) is not int or not 1 <= max_items <= 10:
                    raise ValueError("max_items 必须为 1 至 10")
                catalog = list_platform_resources(mysql, student_id=scope.student_id)
                if not any(
                    item.get("uri") == uri and section in (item.get("sections") or ["overview"])
                    for item in catalog.get("resources") or [] if isinstance(item, dict)
                ):
                    raise ValueError("URI 或 section 不在当前用户可读目录中")
                data = build_evidence_pack(
                    mysql, student_id=scope.student_id, question="",
                    resource_refs=[{"uri": uri, "section": section}],
                    max_items=max_items, max_chars=3500,
                )
                data["success"] = data["status"] in {"completed", "partial"}
                data["row_count"] = len(data["items"])
            else:
                raise ValueError("operation 仅支持 list/read/evidence")
            return json.dumps(data, ensure_ascii=False, default=str)
        except (LookupError, ValueError) as exc:
            return json.dumps({"success": False, "status": "unavailable", "message": str(exc)}, ensure_ascii=False)
        except Exception:
            return json.dumps({"success": False, "status": "failed", "message": "当前业务资料暂时无法读取"}, ensure_ascii=False)
