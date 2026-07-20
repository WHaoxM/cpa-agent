# CPA-Agent Agent Guide

## Current Project Truth

CPA-Agent is currently a competition/research-oriented open-source project with:

- a Flask backend core that is being hardened for real local deployment;
- a Vue frontend demo that still uses local mock data unless a task explicitly asks for backend integration;
- configuration loaders for rules, prompts, dictionaries, integrations, schemas, workspace seeds, and branding;
- a narrowed workspace runtime boundary: only rules overrides and prompt tone overrides are treated as live capabilities.

Do not assume every document in `docs/` is current. Prefer this file, `CONTEXT.md`, `docs/README.md`, and accepted ADRs as navigation entry points.

## Canonical Context Files

Read these first when planning non-trivial work:

1. `CONTEXT.md` — domain glossary, accepted language, project decisions, current phase notes.
2. `docs/README.md` — documentation map and freshness policy.
3. `docs/adr/` — durable architecture decisions.
4. `README.md` — public-facing project overview; verify claims against code before relying on it.

## Working Rules

- Use `rg` / `rg --files` for discovery.
- Use `apply_patch` for file edits. Do not use shell commands to modify files.
- Shell reads must set UTF-8 output in PowerShell:
  `[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; $OutputEncoding = [System.Text.Encoding]::UTF8; ...`
- Treat the worktree as dirty. Do not revert unrelated changes.
- Do not expand workspace features unless the task explicitly asks for it. Current live workspace boundary is:
  - `workspace/<id>/overrides/rules.yaml`
  - `workspace/<id>/overrides/prompts/<prompt_id>/tone.md`
- `workspace/seeds` and `workspace/branding` are deferred loader/sample capabilities, not live product promises.

## Documentation Maintenance Rules

When changing code behavior, update docs in this order:

1. `CONTEXT.md` only if a domain term, accepted boundary, or phase decision changed.
2. ADR only if the decision is hard to reverse, surprising without context, and involved a real trade-off.
3. `docs/README.md` if document ownership, freshness, or navigation changes.
4. A focused guide under `docs/guides/`, `docs/runbooks/`, `docs/specs/`, or `docs/architecture/` when maintainers need procedures or structure.
5. `README.md` only for public-facing setup/status claims.

Archive old material instead of silently deleting it. Use `docs/archive/YYYY-MM/` for stale generated docs, old diagrams, and one-off notes that are no longer source of truth.

## Project Priorities

Current hardening priority:

1. Make the default backend path runnable: app creation, blueprint registration, LangGraph default chat, and basic health checks.
2. Align README / deployment docs / code reality.
3. Add smoke tests for startup, routes, prompt registry, and core pipeline entry points.
4. Clean repository hygiene: ignore env files, caches, model artifacts, generated docs, and temporary Word revisions.
5. Only then consider deeper refactors or frontend-backend integration.

## Frontend Boundary

`cpa-xingtu/` is currently a mock-data frontend demo. Its own `cpa-xingtu/AGENTS.md` is authoritative for frontend-only work.

Do not introduce real API calls into the frontend unless the user explicitly asks for integration work and backend endpoint contracts are verified.

