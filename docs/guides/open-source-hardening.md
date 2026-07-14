# Open-Source Runnable Hardening Guide

## Purpose

This guide defines the next engineering direction for CPA-Agent: make the project credible to clone, configure, start, and smoke-test before adding more product features.

Audience: maintainers and coding agents.

## Current Project Shape

CPA-Agent currently has:

- a Flask backend with configuration and prompt registry work mostly in place;
- a Vue frontend demo that still relies on local mock data;
- multiple pipeline and API entry points with overlapping responsibilities;
- documentation that mixes current architecture, generated diagrams, logs, and competition material.

The next phase should harden runtime truth and documentation truth together.

## Goals

- Backend app can be created and health-checked in a clean environment.
- Default Agent path does not fail on import or missing symbols.
- Registered blueprints match documented API surfaces.
- README and deployment docs describe the project honestly.
- The repository has enough hygiene for open-source use.
- Smoke tests guard startup, prompt registry, routes, and one core pipeline path.

## Non-goals

- Do not expand workspace beyond rules and prompt tone.
- Do not connect frontend mock views to backend unless explicitly scoped.
- Do not rewrite the entire pipeline architecture in one pass.
- Do not treat old generated docs as current source of truth.

## Priority Order

### P0: Backend Startup And Default Paths

Fix first:

- Flask `create_app()` import and blueprint registration.
- LangGraph default chat path.
- Invalid imports such as nonexistent service modules.
- Any missing prompt imports in runtime paths.

Validation:

```powershell
pytest backend/tests/config
python -c "from backend.app import create_app; app=create_app(); print(app.url_map)"
```

### P1: Route Surface Calibration

Create a route smoke test that verifies expected blueprints are registered.

Decide for each API module:

- active public API;
- internal API;
- demo/legacy API;
- not registered.

Update README and docs to match.

### P2: Repository Hygiene

Add or repair ignore rules for:

- `.env`
- `__pycache__/`
- `.pytest_cache/`
- model weights and cache directories;
- generated Word/PDF/HTML outputs;
- local database/vector files;
- archive zip bundles.

Do not delete large files casually. First classify them and document the archive policy.

### P3: Documentation Calibration

Update:

- root README current status;
- deployment guide;
- docs map;
- AGENTS.md;
- relevant ADRs only when a durable decision changes.

### P4: Targeted Tests

Add tests for:

- app factory smoke;
- prompt registry load and scan;
- route map registration;
- config loader boundaries;
- one pipeline smoke test with no external LLM requirement.

## Open Questions

- Should `career_bp` be registered as an active API or documented as future/internal?
- Should LangGraph remain default if function calling support depends on the configured provider?
- Which pipeline endpoint is the canonical public entry point?
- Which large artifacts should remain in Git versus external release assets?

