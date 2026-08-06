# Local Smoke Test Runbook

## Purpose

Quickly verify that CPA-Agent is runnable enough for maintainer work after code or documentation changes.

Audience: maintainers and coding agents.

## Preconditions

- Python environment available — use the conda env **`mkg`** (Python 3.10), see `README.md` → 运行环境. The default `python` on PATH has no project dependencies.
- Dependencies installed from `backend/requirements.txt`.
- `.env` exists with required LLM and database settings if testing external services.
- `CPA_DEMO_MODE=true` allows local backend startup without LLM, MySQL, Neo4j, Redis, or Milvus being available.
- For no-network smoke checks, avoid routes that call LLM, MySQL, Neo4j, Redis, or Milvus.

## Immediate Checks

Run from repo root:

```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTEST_ADDOPTS='-p no:cacheprovider'
python -B -m pytest backend/tests/config backend/tests/test_app_smoke.py backend/tests/test_demo_endpoints.py
```

Check Flask app creation:

```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
python -B -c "import sys; sys.path.insert(0, 'backend'); from app import create_app; app=create_app(); print('/health' in [str(r) for r in app.url_map.iter_rules()])"
```

## Route Map Inspection

```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
python -B -c "import sys; sys.path.insert(0, 'backend'); from app import create_app; app=create_app(); [print(r) for r in sorted(str(x) for x in app.url_map.iter_rules())]"
```

Confirm documented route groups are present:

- `/health`
- `/api/agent/*`
- `/api/pipeline/*`
- `/api/pipeline/ext/*`
- `/api/profile/*`
- `/api/match/*`
- `/api/report/*`
- `/api/graph/*`
- `/api/admin/*`
- `/api/learning/*`
- `/api/favorites` or equivalent favorites routes

## Golden Demo Path Checks

These checks do not require MySQL, Redis, Neo4j, Milvus, or an LLM provider:

```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
python -B -m pytest backend/tests/test_demo_endpoints.py
```

Expected behavior:

- `/api/pipeline/list` returns the L0-L4 catalog.
- `/api/agent/chat` with `mode=demo` returns `source: demo_fallback`.
- `/api/career/landscape` returns demo career landscape if MySQL is unavailable.
- `/api/admin/knowledge-base/smart-upload` can extract known skills/roles and skip persistence if MySQL is unavailable.

## Real Service Checks

Start the dev database stack from repo root:

```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d mysql neo4j redis
```

If MySQL seed data was reloaded, clear API caches that can outlive the database volume:

```powershell
docker exec cpa-agent-dev-redis-1 redis-cli DEL cache:career:landscape
```

Check the three database clients and representative API routes:

```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
cd backend
python -B -c "from app.services.db import get_mysql_client,get_redis_client,get_neo4j_client; print(get_mysql_client().test_connection(), get_redis_client().test_connection(), get_neo4j_client().test_connection())"
python -B -c "from app import create_app; c=create_app().test_client(); print(c.get('/health').status_code); print(c.get('/api/career/landscape').get_json()['data'][0]['category'])"
```

For frontend real-service checks:

```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
cd cpa-xingtu
Copy-Item .env.example .env.local -Force
npm run dev
```

Open `http://127.0.0.1:5173/cpa-xingtu/`, log in with the demo account, then verify:

- `student/career-analysis` requests `GET http://127.0.0.1:5001/api/career/landscape`.
- `student/ai-assistant` requests `POST http://127.0.0.1:5001/api/agent/chat`.
- `student/career-navigation` requests `POST http://127.0.0.1:5001/api/pipeline/ext/parse/resume`, `POST http://127.0.0.1:5001/api/pipeline/trigger`, and `GET http://127.0.0.1:5001/api/pipeline/status/{task_id}`.
- `student/career-report` saves reports through `POST http://127.0.0.1:5001/api/report/generate`.
- `student/my-reports` loads the bookshelf through `GET http://127.0.0.1:5001/api/report/list` and opens a book through `GET http://127.0.0.1:5001/api/report/{report_id}`.

## Start Backend Locally

For local demo mode:

```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:CPA_DEMO_MODE='true'
python -B backend/run.py
```

Then open:

- `http://localhost:5001/health`
- `http://localhost:5001/api/pipeline/list`
- `http://localhost:5001/api/report/list`

For production-like validation, set `CPA_DEMO_MODE=false` and provide real `LLM_API_KEY`, `NEO4J_PASSWORD`, and database settings.

## Prompt Registry Check

```powershell
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
python -B -m pytest backend/tests/config/test_prompt_loader.py
```

## Failure Triage

If app creation fails:

1. Check missing imports in default runtime paths.
2. Check prompt registry entry paths.
3. Check configuration loader errors.
4. Check blueprint imports in `backend/app/api/__init__.py`.

If route map is missing expected APIs:

1. Check whether the blueprint is defined.
2. Check whether the module is imported in `backend/app/api/__init__.py`.
3. Check whether the blueprint is registered in `backend/app/__init__.py`.
4. Decide whether the route is active, internal, legacy, or future.

## Escalation

If a smoke failure depends on external services, split it into:

- import/startup failure;
- database connectivity failure;
- LLM provider failure;
- data initialization failure.

Do not hide external-service failures behind broad `except Exception` unless the route is explicitly best-effort.
