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

## Runtime Environment (Read Before Running Anything)

Before executing any backend command or test, read the **运行环境（Agent / 开发者必读）** section in `README.md`. Key facts:

- The authoritative backend runtime is the conda env **`mkg`** (Python 3.10). The default `python` on PATH (system Python 3.11) has **no project dependencies** — running tests with it fails immediately with `ModuleNotFoundError: No module named 'flask'`. The `cpa-agent` conda env (Python 3.11) is incomplete (no langgraph) and must not be used.
- Run tests from the repo root with the `mkg` env: `python -B -m pytest backend/tests`. Windows fallback without `conda` on PATH: `D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m pytest backend/tests`.
- DB-backed integration tests require the docker containers (MySQL `localhost:13306` / Neo4j `localhost:17687` / Redis `localhost:16379`). When they are down, expect connection-refused failures in `test_hybrid_retrieve_explain.py` and `test_demo_endpoints.py`.
- Repo-root `.env` currently sets `CPA_DEMO_MODE=false` (strict mode): user-resource APIs require an auth Principal or they return 401.

## Working Rules

- Use `rg` / `rg --files` for discovery.
- Use `apply_patch` for file edits. Do not use shell commands to modify files.
- **Agent 基础能力开源优先（硬规则）**：修改 Agent 数据流、工具协议、文件/多模态解析、任务状态、上下文管理、token 预算、压缩、权限或错误处理前，先检查项目现有实现与可获取的成熟开源 Agent 实现（本机参考快照有 `opencode/`、工作区外 `_agent_reference/codex/`，均不随本仓分发），记录所参考的仓库、commit 或发布版本、具体模块及适配边界。优先直接复用经过验证的库、协议和状态机，以薄适配层接入本项目；不得在已有成熟方案可用时另造平行的通用轮子，或仅凭直觉重写其核心数据流。确需自实现时，先写明现成方案不适用的具体约束与验证证据。不得把“照抄”理解为忽略许可证、项目业务边界或运行时验证。
- **复用验收门槛**：每次 Agent 基础能力改造都要以实际请求链路证明输入、状态转移、预算/截断、工具输出和最终结果；超预算或部分解析必须显式标记并提供继续读取/重试路径，不能静默丢内容、假称全文或只用源码推断运行效果。
- **Encoding (Windows / PowerShell 5.1) — read this first, see `docs/guides/windows-powershell-encoding.md`:**
  - Repo text files are all UTF-8 and are NOT corrupted. Mojibake you see in the terminal is a PowerShell 5.1 decoding artifact, not a file problem.
  - Read file content with the Read/Edit tools, or `Get-Content -Encoding UTF8 <file>`. Do **not** use bare `Get-Content` / `type` / `cat` to read Chinese text — PS 5.1 decodes as ANSI/GBK and shows garbage.
  - When running Python/native commands, set the UTF-8 preamble first:
    `[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; $OutputEncoding = [System.Text.Encoding]::UTF8; $env:PYTHONUTF8='1'`
  - Never "fix" a garbled file by re-encoding its bytes; first verify with a UTF-8 decode check (see guide).
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

