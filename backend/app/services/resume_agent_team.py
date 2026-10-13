"""Domain-specific resume agent team for the opt-in asynchronous L0 route.

Each specialist owns a real operation and returns an artifact to the next one.
The older synchronous L0 path keeps its existing all-in-one behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict


@dataclass(frozen=True)
class DocumentArtifact:
    text: str
    blocks: list
    metadata: dict
    engine: str


class DocumentAgent:
    """Select document/OCR engine and produce an evidence-bearing text artifact."""

    def run(self, *, file_path=None, text=None) -> DocumentArtifact:
        from ..pipeline.entity_extractor.l0_full_parser import L0FullParser

        parser = L0FullParser()
        if file_path:
            result = parser.parse_file(file_path)
        elif text:
            result = parser.parse_text(text)
        else:
            raise ValueError("缺少 file_path 或 text 参数")
        if not result.success:
            raise RuntimeError(result.error or "文件解析失败")
        if not result.text.strip():
            raise ValueError("未提取到简历正文")
        return DocumentArtifact(text=result.text, blocks=result.blocks,
                                metadata=result.metadata or {}, engine=result.engine)


class StructureAgent:
    """Call the canonical resume model without silently doing entity work."""

    def run(self, document: DocumentArtifact):
        from ..pipeline.entity_extractor.nsle_pipeline import NSLEPipeline

        pipeline = NSLEPipeline(use_deepseek=True)
        structured = pipeline.run_resume_text(document.text, link_entities=False)
        profile = structured.get("student_profile") or {}
        if not profile:
            raise RuntimeError("简历结构化失败；未修改已确认资料")
        return structured, pipeline.llm_client


class EntityAgent:
    """Perform the existing grounded skill-mention and entity-linking calls."""

    def run(self, profile: Dict[str, Any], llm_client, document: DocumentArtifact) -> Dict[str, Any]:
        from ..pipeline.entity_extractor.resume_structure import link_profile_entities

        return link_profile_entities(profile, llm_client, document.text)


class EvidenceAgent:
    """Assemble verifiable source blocks and page coverage; never score."""

    def run(self, document: DocumentArtifact, profile: Dict[str, Any]) -> dict:
        blocks = [{"block_id": block.block_id, "text": block.text,
                   "page": block.page + 1, "block_type": block.block_type}
                  for block in document.blocks]
        page_count = document.metadata.get("pages") or document.metadata.get("page_count")
        observed = sorted({block["page"] for block in blocks if block["text"].strip()})
        missing = [page for page in range(1, int(page_count) + 1) if page not in observed] if page_count else []
        return {"blocks": blocks, "page_count": page_count, "observed_pages": observed,
                "missing_pages": missing, "engine": document.engine,
                "coverage": "partial" if missing else "unverified", "accuracy_verified": False,
                "profile_field_count": len(profile)}
