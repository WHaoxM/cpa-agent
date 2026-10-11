"""Read-only, evidence-grounded linking of resume mentions to public skills.

This is a domain adapter over the existing Neo4j client and LLMClient structured
output/budget enforcement, not a second extraction engine or ontology writer.
"""
import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from config.prompt_loader import load_prompt

from .public_skill_graph import SOURCE_ID
from .public_skill_retrieval import retrieve_public_skill_candidates


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    mention_index: int = Field(ge=0)
    decision: Literal["equivalent", "related", "unresolved"]
    candidate_index: int | None
    rationale: str = Field(min_length=1)


class Decisions(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    decisions: list[Decision]


def link_resume_mentions(llm_client, mentions, text, *, client=None):
    """Keep original mentions; never rewrite skills or auto-register aliases.

    Published candidate recall is bounded per mention, independent of catalog
    size. Legacy mixed Skill vectors are never canonical identity evidence.
    """
    result = {"status": "unresolved", "source_id": SOURCE_ID,
              "source_sha256": hashlib.sha256(text.encode()).hexdigest(),
              "mentions": mentions, "candidates": [], "decisions": [],
              "next_action": "retry_entity_linking"}
    if not mentions:
        return {**result, "reason": "no_grounded_mentions"}
    if len(mentions) > 100:
        return {**result, "reason": "mention_budget_exceeded"}
    if any(not m.get("name") or not m.get("evidence") or m["evidence"] not in text for m in mentions):
        return {**result, "reason": "evidence_not_in_source"}
    try:
        if client is None:
            from .db import get_neo4j_client
            client = get_neo4j_client()
        candidates, retrieval = retrieve_public_skill_candidates(client, mentions)
        result["retrieval"] = retrieval
        result["candidates"] = candidates
        if not candidates:
            return {**result, "reason": "no_retrieved_public_candidates"}
        prompt = load_prompt("entity.resume_link.user", mentions_json=json.dumps(mentions, ensure_ascii=False),
                             candidates_json=json.dumps(candidates, ensure_ascii=False))
        kwargs = dict(messages=[{"role": "user", "content": prompt}], temperature=0.1,
            max_tokens=4000, response_format={"type": "json_schema", "json_schema": {
                "name": "resume_entity_links", "strict": True, "schema": Decisions.model_json_schema()}})
        method = getattr(llm_client, "chat_with_metadata", None)
        if callable(method):
            response = method(**kwargs)
            result["call"] = {"usage": response.usage, "finish_reason": response.finish_reason,
                              "provider": response.provider, "model": response.model,
                              "requested_output_tokens": 4000}
            content = response.text
        else:
            content = llm_client.chat(**kwargs)
        decisions = Decisions.model_validate_json(str(content)).decisions
        if sorted(d.mention_index for d in decisions) != list(range(len(mentions))):
            raise ValueError("incomplete_or_duplicate_decisions")
        for decision in decisions:
            index = decision.candidate_index
            if decision.decision == "unresolved":
                if index is not None:
                    raise ValueError("unresolved_has_candidate")
            elif index is None or not 0 <= index < len(candidates):
                raise ValueError("candidate_outside_retrieved_catalog")
        return {**result, "status": "proposed", "decisions": [d.model_dump() for d in decisions],
                "next_action": "confirm_resume"}
    except Exception:
        return {**result, "reason": "entity_linking_unavailable"}
