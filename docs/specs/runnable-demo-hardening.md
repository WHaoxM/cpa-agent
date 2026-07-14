# Runnable Demo Hardening Specification

Status: active  
Owner: CPA-Agent maintainers  
Last updated: 2026-06-18

## Problem

The repository contains a working backend shape, a substantial Vue frontend, configuration loaders, prompt registry work, and many historical competition artifacts. The next useful milestone is not more architecture expansion. It is a complete runnable project whose core demo effects can be verified repeatedly.

## Goal

Deliver a Runnable Demo: local backend starts, core route map loads without import errors, configuration and prompt contracts pass tests, and the Golden Demo Path can be exercised for a public/open-source reviewer.

## Non-Goals

- Do not wire workspace seeds or branding into runtime unless the Golden Demo Path already passes.
- Do not refactor the full pipeline before route/app startup is stable.
- Do not treat archived competition documents or generated diagrams as release-blocking source of truth.
- Do not require all external services to be available for basic startup and route-map smoke tests.

## Golden Demo Path

1. Backend application factory creates a Flask app without import errors.
2. `/health` returns a successful JSON response.
3. Core blueprints are registered:
   - `/api/agent/chat`
   - `/api/pipeline/list`
   - `/api/career/landscape`
   - `/api/report/generate`
   - `/api/admin/knowledge-base/smart-upload`
4. Config and prompt registry tests pass.
5. Frontend main student/admin demo pages render and match the documented visible effects.

## Challenge Review

1. If the app cannot import, deeper feature work is noise.
2. If route registration is incomplete, the frontend/API docs will lie even when individual modules exist.
3. If prompt/config tests pass but runtime code imports old paths, the migration is only partial.
4. If smoke tests require MySQL, Neo4j, Milvus, Redis, and an LLM just to create the app, open-source reviewers will fail before seeing the product.
5. If docs do not name the Golden Demo Path, later agents will optimize random subsystems instead of the current completion target.

## Execution Plan

### P0: Startup and Route Smoke

- Fix stale imports that block runtime modules.
- Register existing blueprints that are already defined but unreachable.
- Add smoke tests for app creation, `/health`, and route-map coverage.
- Keep these tests free of external service calls.

Status: done. Covered by `backend/tests/test_app_smoke.py`.

### P1: Backend Demo Behavior

- Add one stable demo payload per Golden Demo Path endpoint.
- Make failures explicit when optional dependencies are missing.
- Keep LLM/database integration behind route-level calls, not app import.
- Use `mode=demo` for Agent chat when no LLM/Redis-backed session is available.
- Return `source: demo_fallback` when Career/Admin demo endpoints fall back from unavailable storage.
- In demo mode, make Career Report generation produce a completed, persisted `career_report` row for save/list/detail checks.
- Keep portrait scoring aligned with frozen D1-D4 rules and regression tests.

Status: done for backend Golden Demo Path. Covered by `backend/tests/test_demo_endpoints.py`, `backend/tests/pipeline/test_portrait_generator.py`, and config contract tests.

### P2: Frontend Effect Pass

- Verify the main visible pages in desktop and mobile viewports.
- Align frontend API assumptions with registered backend routes.
- Document mock-data boundaries where frontend effects are not yet backed by live services.

## Validation Commands

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTEST_ADDOPTS='-p no:cacheprovider'
python -B -m pytest backend/tests
```

## Completion Criteria

- Startup and route smoke tests pass.
- Config/prompt tests pass.
- README and docs do not claim runtime capabilities beyond the Golden Demo Path.
- Remaining gaps are tracked as follow-up hardening items, not hidden in stale docs.
