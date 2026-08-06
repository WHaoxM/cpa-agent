# ADR 0002 — Pattern upgrade: retrieve≠score, parse Sidecar, agent freeze

Status: Accepted  
Date: 2026-07-19

## Context

CPA-Agent hardening needs OSS pattern reuse without forking platforms (Dify/FastGPT/CrewAI). Grill-locked decisions require:

- Deterministic match scores from rules/code only
- Hybrid retrieve (vector + keyword + graph hop) must not write scores
- Heavy PDF parse externalized to a Sidecar; in-process MinerU retired from `auto`
- Agent topology frozen (no new subagents); harden gates/budget instead

## Decision

1. **Score / explain separation** — `SkillMatcher` and `match_result` remain authoritative. `/api/match/explain` and hybrid retrieve expose evidence, keyword coverage, RRF components, and Neo4j paths only.
2. **L0 ladder** — `CPA_L0_PDF_ENGINE=auto` → Sidecar (`CPA_L0_PARSE_SIDECAR_URL`) → PyMuPDF. MinerU only when explicitly `CPA_L0_PDF_ENGINE=mineru`.
3. **Agent freeze** — `FROZEN_SUBAGENTS` allowlist; new expert roles require an ADR, not a silent register.

## Consequences

- Golden demo stays runnable without Sidecar (PyMuPDF fallback / DISABLED).
- Frontend (`cpa-xingtu`) consumes explain/hybrid contracts; Vue mock demo out of scope.
- LiteLLM + Langfuse + promptfoo attach as LLMOps layer without changing score semantics.
