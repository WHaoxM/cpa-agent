# Documentation Governance Guide

## Purpose

This guide defines how CPA-Agent documentation should be organized, refreshed, and archived so engineers and agents can build from accurate context.

Audience: maintainers and coding agents.

## Core Rule

Separate four document surfaces:

1. **Context** — stable domain language and accepted boundaries.
2. **Decision records** — why durable choices were made.
3. **Working guides** — how maintainers operate or change the system now.
4. **Archive** — useful history that is not current authority.

Do not let logs, generated diagrams, competition drafts, and current engineering guides compete as equal sources of truth.

## Document Types

### Context

File:

- `CONTEXT.md`

Use for:

- domain terms;
- accepted project boundaries;
- current phase decisions;
- ambiguity resolutions.

Do not use for:

- implementation walkthroughs;
- commands;
- historical logs;
- one-off task notes.

### ADRs

Directory:

- `docs/adr/`

Use an ADR only when all are true:

- hard to reverse;
- surprising without context;
- involved real alternatives.

ADRs are not rewritten silently. If a decision changes, create a new ADR or clearly mark the older one as superseded.

### Architecture Docs

Directory:

- `docs/architecture/`

Use for current component boundaries, request/data flow, dependencies, and known limits.

Architecture docs must stay higher-level than code comments. Link to files instead of duplicating long code descriptions.

### Specs

Directory:

- `docs/specs/`

Use before implementing a non-trivial change. Include goals, non-goals, constraints, design, risks, validation, and rollback where relevant.

### Runbooks

Directory:

- `docs/runbooks/`

Use for fast operational action: local smoke checks, deployment checks, recovery steps, and incident triage.

### Guides

Directory:

- `docs/guides/`

Use for maintainer workflows: adding prompts, changing rules, cleaning docs, or preparing public releases.

### Archive

Directory:

- `docs/archive/YYYY-MM/`

Use for stale diagrams, generated reports, old architecture snapshots, temporary drafts, and historical notes.

## AGENTS.md Update Policy

`AGENTS.md` is the short operational guide for coding agents. Keep it compact.

Update it when:

- the current project truth changes;
- the live workspace boundary changes;
- the documentation source-of-truth order changes;
- default implementation priorities change;
- frontend/backend boundaries change.

Do not copy full architecture docs into `AGENTS.md`. Link to the relevant doc.

Suggested review cadence:

- weekly during active hardening;
- before any long autonomous agent task;
- after every accepted ADR that changes agent behavior.

## Archive Workflow

1. Identify stale docs and the current replacement.
2. Move stale docs into `docs/archive/YYYY-MM/`.
3. Add an archive header to Markdown files.
4. Update `docs/README.md` if navigation changed.
5. Do not archive `CONTEXT.md`, active ADRs, or files needed by build/test tooling.

## Freshness Labels

Use these labels at the top of maintained Markdown docs when useful:

```markdown
---
status: active | draft | superseded | archived
last_reviewed: YYYY-MM-DD
owner: maintainer | backend | frontend | docs
---
```

For short files, a plain sentence is enough.

## Current Known Documentation Gaps

- Root README overstates frontend-backend integration relative to `cpa-xingtu/AGENTS.md`.
- Some architecture docs are generated snapshots and need consolidation.
- Docker/deployment docs must be checked against `docker-compose.yml` and `.env.example`.
- Workspace seeds/branding must remain documented as deferred capabilities, not live runtime promises.

