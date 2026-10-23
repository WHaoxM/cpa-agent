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

## Agent Update Loop

The goal is to keep one understandable current home per topic, not to grow the document count.

### Start: find the owner before writing

1. Read `docs/current-work.md` for active handoffs and `docs/README.md` for navigation.
2. Search by user goal, capability, API/domain name, and likely document title. Read the existing owner document and check its status against code/runtime before relying on it.
3. Classify the needed update: durable domain language (`CONTEXT.md`), lasting trade-off (ADR), planned product contract (`specs/`), reusable maintainer procedure (`guides/` or `runbooks/`), or evidence from an execution (`reports/`). A plan is not a report; test output is not a product contract.

### During: update in place by default

- Add a new user scenario to the existing capability/acceptance document that owns that business journey. Do not create a sibling Guide just to hold a second scenario list or a portfolio table.
- Create a new Guide only when it describes a recurring procedure used across multiple capabilities and no current Guide owns that procedure. Create a new Spec only for a material, independently reviewable product/architecture proposal.
- Keep one active owner document for one subject. If two active docs repeat the same rules, choose the canonical home, merge useful facts, then mark the duplicate superseded or archive it with a pointer; do not leave both looking authoritative.
- Runtime behavior and current code outrank old reports. Reports preserve what happened on a specific date; never edit an old report to make it reflect a later run.

### Close: record only what was proved

- After a real verification run, write/update a dated report with exact command, code/runtime context, user journey, authoritative result, services vs substitutes, cleanup, and uncovered scope. Mark skips and unavailable dependencies **not accepted**, not pass.
- Link the newest report from the existing owner Guide or active handoff. Keep `docs/README.md` focused on stable navigation and document ownership; do not append every run report to every index.
- If full stdout, request traces, screenshots, or result artifacts were not retained, label the report as a summary and say so. Never reconstruct details as if they were raw evidence.
- Update `docs/current-work.md` only when the result changes active handoff, priority, or unresolved status. Keep it a short pointer, not another copy of report details.

### Periodic cleanup

When reopening a topic, check whether the owner doc is still current and whether nearby drafts/reports duplicate it. Update or supersede stale content as part of the task when the evidence is available; otherwise mark the exact gap and owner. Do not perform speculative bulk archival unrelated to the current task.

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
- The backend business-journey acceptance material has one owner guide; scenario expansions belong there, while each actual dated replay belongs in `docs/reports/` and is linked from that guide.

