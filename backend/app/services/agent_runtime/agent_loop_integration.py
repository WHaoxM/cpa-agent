"""Agent Loop 集成模块 - 将SessionRunner与现有pipeline集成"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
import logging

logger = logging.getLogger(__name__)


class AgentLoopIntegration:
    """Agent Loop 集成模块"""

    def __init__(self, llm_client: Any = None):
        self.llm_client = llm_client

        from .session_runner import SessionRunner
        from .session_input import SessionInput
        from .tool_registry import ToolRegistry
        from .session_compaction import SessionCompaction

        self.session_input = SessionInput()
        self.tool_registry = ToolRegistry()
        self.compaction = SessionCompaction(llm_client)

        self.runner = SessionRunner(
            llm_client=llm_client,
            tool_registry=self.tool_registry,
            session_input=self.session_input,
            compaction=self.compaction,
        )

        self._register_builtin_tools()

    def _register_builtin_tools(self):
        from .tool_registry import ToolPermission

        self.tool_registry.register(
            name="parse_resume",
            description="解析简历文本，提取基本信息",
            input_schema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
            output_schema={"type": "object", "properties": {"success": {"type": "boolean"}, "data": {"type": "object"}}},
            executor=self._parse_resume_tool,
        )

        self.tool_registry.register(
            name="extract_skills",
            description="从文本中抽取技能",
            input_schema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
            output_schema={"type": "object", "properties": {"skills": {"type": "array"}}},
            executor=self._extract_skills_tool,
        )

        self.tool_registry.register(
            name="normalize_entities",
            description="归一化抽取的实体",
            input_schema={"type": "object", "properties": {"entities": {"type": "object"}}, "required": ["entities"]},
            output_schema={"type": "object", "properties": {"normalized": {"type": "object"}}},
            executor=self._normalize_entities_tool,
        )

    def _parse_resume_tool(self, text: str) -> Dict[str, Any]:
        try:
            from app.pipeline.entity_extractor.nsle_pipeline import NSLEPipeline
            pipeline = NSLEPipeline(use_deepseek=True)
            result = pipeline.process(text)
            return {"success": True, "data": result}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _extract_skills_tool(self, text: str) -> Dict[str, Any]:
        try:
            from app.pipeline.entity_extractor.llm_skill_extractor import LLMSkillExtractor
            extractor = LLMSkillExtractor()
            skills = extractor.extract(text, use_llm=True)
            return {"skills": skills}
        except Exception as e:
            return {"skills": [], "error": str(e)}

    def _normalize_entities_tool(self, entities: Dict[str, Any]) -> Dict[str, Any]:
        try:
            from app.utils.normalization_agent import get_normalization_supervisor
            supervisor = get_normalization_supervisor(self.llm_client)
            result = supervisor.normalize_all(entities)
            normalized = {}
            for entity_type, batch_result in result.items():
                normalized[entity_type] = [
                    {"original": r.original, "normalized": r.normalized, "confidence": r.confidence, "is_known": r.is_known}
                    for r in batch_result.results
                ]
            return {"normalized": normalized}
        except Exception as e:
            return {"normalized": {}, "error": str(e)}

    def parse_resume_with_agent_loop(self, resume_text: str, session_id: str = "default", max_steps: int = 5):
        initial_messages = [{"role": "user", "content": f"请解析以下简历并提取关键信息：\n\n{resume_text[:3000]}\n\n请使用工具完成：parse_resume -> extract_skills -> normalize_entities"}]
        system_prompt = "你是简历解析助手。按步骤使用工具：1.parse_resume解析简历 2.extract_skills抽取技能 3.normalize_entities归一化。输出完整结构化结果。"
        return self.runner.run(session_id=session_id, initial_messages=initial_messages, system_prompt=system_prompt, agent_config={"max_steps": max_steps})

    def submit_input(self, session_id: str, content: str, delivery: str = "queue"):
        from .session_input import DeliveryMode
        mode = DeliveryMode.STEER if delivery == "steer" else DeliveryMode.QUEUE
        self.session_input.admit(session_id, content, mode)

    def get_status(self, session_id: str) -> Dict[str, Any]:
        return {"pending_inputs": self.session_input.get_pending_count(session_id), "tools_count": len(self.tool_registry.list_tools())}


_agent_loop: Optional[AgentLoopIntegration] = None


def get_agent_loop(llm_client: Any = None) -> AgentLoopIntegration:
    global _agent_loop
    if _agent_loop is None:
        _agent_loop = AgentLoopIntegration(llm_client)
    return _agent_loop
