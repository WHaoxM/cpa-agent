"""Evidence-backed semantic extraction, without dictionary/keyword fallbacks."""
from typing import Dict, List, Literal
import json

from pydantic import BaseModel, ConfigDict, Field
from config.prompt_loader import load_prompt


class SkillExtractionError(RuntimeError):
    """An unavailable/invalid extraction is not an authoritative empty list."""


class SkillCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1)
    level: Literal["了解", "熟悉", "掌握", "精通", "未说明"]
    category: str = Field(min_length=1)
    skill_type: Literal["hard", "soft"]
    confidence: float = Field(ge=0, le=1)
    evidence: str = Field(min_length=1)


class SkillRelation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    source: str = Field(min_length=1)
    target: str = Field(min_length=1)
    type: str = Field(min_length=1)
    evidence: str = Field(min_length=1)


class SkillExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    skills: List[SkillCandidate]
    relations: List[SkillRelation]


class SkillMention(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1)
    source_segment_index: int = Field(ge=0)


class SkillMentions(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    mentions: List[SkillMention]


class LLMSkillExtractor:
    """Model candidates with verbatim source evidence, not catalog identities.

    use_normalizer is retained for constructor compatibility only. Automatic
    alias rewriting/dictionary mutation is deliberately not applied.
    """

    def __init__(self, use_normalizer: bool = True):
        self._llm_client = None

    @property
    def llm_client(self):
        if self._llm_client is None:
            from app.utils.llm_client import get_llm_client
            self._llm_client = get_llm_client()
        return self._llm_client

    def extract(self, text: str, use_llm: bool = True) -> List[Dict]:
        return self.extract_document(text, use_llm=use_llm)["skills"]

    def extract_mentions(self, text: str) -> Dict:
        """Narrow enrichment output: do not duplicate levels/categories/relations.

        Full source input is retained; an over-budget or truncated response is
        explicit failure handled by the caller, not a clipped list of mentions.
        """
        segments = text.splitlines(keepends=True) or [text]
        prompt = load_prompt("entity.skill_mentions.user", text=json.dumps(
            [{"index": i, "text": line} for i, line in enumerate(segments)], ensure_ascii=False))
        kwargs = dict(messages=[{"role": "user", "content": prompt}], temperature=0.1,
            max_tokens=4000, response_format={"type": "json_schema", "json_schema": {
                "name": "skill_mentions", "strict": True, "schema": SkillMentions.model_json_schema()}})
        metadata = {"input_chars": len(text), "requested_output_tokens": 4000, "usage": None,
                    "finish_reason": None}
        call = getattr(self.llm_client, "chat_with_metadata", None)
        if callable(call):
            response = call(**kwargs)
            metadata.update(usage=response.usage, finish_reason=response.finish_reason,
                            provider=response.provider, model=response.model)
            content = response.text
        else:
            content = self.llm_client.chat(**kwargs)
        result = SkillMentions.model_validate_json(str(content))
        grounded = []
        issues = []
        for index, item in enumerate(result.mentions):
            source_index = item.source_segment_index
            if source_index >= len(segments) or not segments[source_index].strip():
                issues.append({"index": index, "reason": "invalid_source_segment"})
            else:
                start = sum(len(s) for s in segments[:source_index])
                grounded.append({"name": item.name, "evidence": segments[source_index],
                                 "source_segment_index": source_index, "source_start": start,
                                 "source_end": start + len(segments[source_index])})
        return {"mentions": grounded, "call": metadata, "issues": issues,
                "status": "partial" if issues else "completed"}

    def extract_document(self, text: str, use_llm: bool = True) -> Dict:
        if not use_llm:
            raise SkillExtractionError(
                "semantic_extraction_required: dictionary fallback is disabled; retry with a configured model"
            )
        if not text or not text.strip():
            return {"skills": [], "relations": [], "input_chars": len(text or "")}
        # Full input: existing LLMClient rejects window/turn overflow and
        # truncated output, rather than silently accepting a head-slice.
        prompt = load_prompt("entity.skill_extractor.user", text=text)
        try:
            response = self.llm_client.chat(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=4000,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "skill_extraction",
                        "strict": True,
                        "schema": SkillExtraction.model_json_schema(),
                    },
                },
            )
            # Pydantic validates the whole JSON; no regex JSON repair.
            result = SkillExtraction.model_validate_json(str(response))
            for skill in result.skills:
                if skill.evidence not in text:
                    raise ValueError("skill evidence is absent from the source")
            names = {skill.name for skill in result.skills}
            for relation in result.relations:
                if relation.source not in names or relation.target not in names:
                    raise ValueError("relation endpoint is not an extracted skill")
                if relation.evidence not in text:
                    raise ValueError("relation evidence is absent from the source")
        except Exception as exc:
            raise SkillExtractionError(
                "semantic_extraction_failed: retry extraction; no candidates were accepted"
            ) from exc
        return {
            "skills": [dict(item.model_dump(), source="llm_candidate") for item in result.skills],
            "relations": [item.model_dump() for item in result.relations],
            "input_chars": len(text),
        }


def extract_skills(text: str, use_llm: bool = True) -> List[Dict]:
    return LLMSkillExtractor().extract(text, use_llm=use_llm)


def extract_skills_with_relations(text: str, use_llm: bool = True) -> Dict:
    return LLMSkillExtractor().extract_document(text, use_llm=use_llm)
