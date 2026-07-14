# CPA-Agent Documentation Map

This directory contains active engineering docs, historical notes, generated diagrams, and competition-writing material. Not every file here is current source of truth.

## Source-Of-Truth Order

Use this order when documents disagree:

1. `../CONTEXT.md` — domain terms, accepted boundaries, phase decisions.
2. `adr/` — durable architecture decisions.
3. This file — documentation organization and freshness policy.
4. Focused guides, runbooks, specs, and architecture docs under the active folders below.
5. Historical logs, generated diagrams, and archived drafts.

## Active Documentation Layers

| Layer | Directory | Purpose | Update trigger |
|---|---|---|---|
| Context | `../CONTEXT.md` | Domain language and accepted decisions | Term, boundary, or phase changes |
| Decisions | `adr/` | Durable architecture decisions | Hard-to-reverse trade-off decisions |
| Architecture | `architecture/` | Current system structure and boundaries | Topology, ownership, or integration changes |
| Specs | `specs/` | Planned changes before implementation | Before coding a non-trivial change |
| Runbooks | `runbooks/` | Operate, diagnose, recover | Operational procedure changes |
| Guides | `guides/` | Maintainer implementation guidance | Repeated maintenance workflow |
| API Notes | `api/` | Internal API contract notes | Endpoint contract or integration changes |
| Archive | `archive/YYYY-MM/` | Stale or superseded docs | Quarterly or after major rewrites |

Folders may be created lazily when the first current document of that type is needed.

## Current Files To Treat Carefully

These files are useful but should not be treated as fully current without verification:

- `dev_log.md`, `bug_log.md` — chronological notes, useful for archaeology.
- `database_design.md`, `arch_v2_db_schema.md` — architecture drafts; the frozen source of truth for DB schemas now lives in `.trae/rules/{mysql,neo4j,milvus}-contract.md`.
- `jd_cleaner_usage.md` — tool usage note; verify against current `cleaning_data/` code before relying on it.

Historical competition material, generated diagrams, arch_v2 layer specs, and personal deliverables have been moved to `archive/2026-07/` and `../archive/personal-deliverables/`.

Early-stage root directories with no remaining value have been archived under `../archive/`:

- `../archive/third-party/MiroFish/` — third-party swarm-intelligence project (only referenced as a design-pattern inspiration in comments and historical docs; no code dependency).
- `../archive/early-prototypes/mvp-entity-extractor/` — early NS-LE pipeline prototype, superseded by `backend/app/pipeline/entity_extractor/`.
- `../archive/early-prototypes/data-samples/` — early JD/resume sample datasets and benchmark JSON outputs, not referenced by current code.
- `../archive/early-prototypes/knowledge_base-sample/` — early `knowledge_base/job_posting.json` sample; `backend/scripts/milvus/generate_vectors.py` notes that the source directory must be rebuilt before running.

## Recommended Active Set

For open-source runnable hardening, keep these active and maintained:

- `../AGENTS.md`
- `../CONTEXT.md`
- `README.md`
- `adr/0001-config-and-workspace-layers.md`
- `guides/open-source-hardening.md`
- `guides/documentation-governance.md`
- `specs/runnable-demo-hardening.md`
- `runbooks/local-smoke-test.md`

## Archive Policy

Archive instead of deleting when a document still has historical value.

Use:

```text
docs/archive/YYYY-MM/<original-name>.md
```

Add a short header to archived Markdown files:

```markdown
> Archived on YYYY-MM-DD.
> Superseded by: <path or reason>
```

Do not archive accepted ADRs. Supersede them with a later ADR if needed.

## Maintenance Cadence

Weekly during active hardening:

- Update `AGENTS.md` if agent operating rules changed.
- Update `CONTEXT.md` if domain boundaries or phase decisions changed.
- Mark stale docs in this map before moving files.

Monthly:

- Move superseded generated diagrams and old notes into `docs/archive/YYYY-MM/`.
- Check `README.md` claims against runnable code.
- Check `DEPLOY.md` against `docker-compose.yml` and `.env.example`.

Before release or public demo:

- Run backend and frontend smoke checks.
- Confirm README setup path still works.
- Confirm public claims do not imply frontend-backend integration if frontend remains mock-based.
