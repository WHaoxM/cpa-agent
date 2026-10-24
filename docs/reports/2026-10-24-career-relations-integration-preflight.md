# Career relation integration preflight — 2026-10-24

## Scope and result

This report records local integration and read-only database validation for the account profile and career relation modules. No database migration, graph projection write, production service restart, remote push, or deployment was performed.

The backend integration worktree is `codex/integration-prep-20261024`. At code-verification time it was at `a97a947196fe06eb6cb58daec34d5f65a2a6aa90`; a documentation-only commit `17be8c8` was added afterward. The branch contains the same-session history fix `336e8b3dcc856c3f93573c5e25eb247854478208`, account profile backend `f2422ccb565d815403b733db213e207ad5a3a1c4`, career relation backend `1a454340057921b37f7b752237e55cc5b3cab7bc`, migration draft `0023_career_relation_projection` (`f6ab1fd5ae264344d2f9de81a101d87728af7b29`), and the profile-FK collation correction in `a97a947`. The worktree was clean after the documentation commit.

The frontend integration worktree is `codex/integration-prep-20261024` at `6ef82faba428dd54b3511f56457f38c7c623eb96`. It contains the published `main` commit `837378d4346c714db7b4d6bc92353bd841a5fb9d` as an ancestor, candidate UI fix `17f8bc4a8cc1a4a804c15f5b225057e826a7b0dd`, profile frontend `2212a11f96013ab10e7dcf02720fa47ce024f308`, career graph frontend `708bdd5c99b143fee5a431a1340670c6b5dc2311`, and the current graph-contract test repair. The integrated branch was not pushed.

## Production source dry-run

Using the existing protected production configuration in process memory, the command asserted its target was `localhost:13306/cpa_agent`, then called `compute_and_persist(mysql, apply=False)`. The script body and raw output were not saved; the following is a summary of the actual output. No configuration values or pathway content were printed.

- MySQL: 8.0.46 Community Server.
- Algorithm/source: `shared-skill-jaccard/v1`, `career_pathway_revision` plus `career_role_skill`.
- Active curated pathways: 1; canonical skills: 8; source evidence present: 8/8; missing skill evidence: 0.
- Preview: 1 node, 0 edges; reason `fewer_than_two_active_pathways`; `writes_performed=false`.
- No separate authoritative complete role catalogue was identified in the inspected source, so the count of roles missing an active pathway cannot be determined. No missing roles or edges were invented.

This source set is insufficient for a useful relationship projection. If it remains unchanged, an apply would add one run row and zero edge rows; no apply was made. The query input is only active pathway revisions, and no separate authoritative complete role catalogue was identified in the inspected source, so the number of roles missing a pathway remains unknown.

## Migration review

Production metadata shows `user_account.id` is non-null `VARCHAR(64)` with `utf8mb4_0900_ai_ci`, while the database default is `utf8mb4_unicode_ci`. The original migration 0022 omitted an explicit child collation, risking a foreign-key mismatch. Local commit `a97a947` changes the migration to read and validate the parent column metadata before DDL, then give `user_account_notification_preference.user_id` the matching character set and collation. It rejects a missing/incompatible parent identity before altering the account table. The account guide now records this requirement.

Production `career_pathway_revision.revision` and `role_id` are both non-null `VARCHAR(64)` with `utf8mb4_unicode_ci`, matching the 0023 relation-table defaults. The production server is MySQL 8.0.46. The integration branch has a single Alembic head, `0023_career_relation_projection`, with the sequence 0021 → 0022 → 0023. Both 0022 and 0023 downgrade functions refuse automatic data-dropping rollback.

Executed static checks:

```powershell
D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m pytest backend\tests\test_account_profile_migration_collation.py backend\tests\test_career_relation_migration_chain.py -q
```

Result: 6 passed. The tests cover metadata-derived collation, fail-closed rejection of incompatible/unsafe parent identity, the single migration head, and relation keys/foreign keys/check constraints/indexes. This is not a real migration rehearsal.

```powershell
D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m pytest backend\tests\test_ask_history_lookup.py backend\tests\test_account_profile_api.py backend\tests\test_account_profile_migration_collation.py backend\tests\test_career_relations.py backend\tests\test_career_relation_migration_chain.py -q
```

Result: 32 passed in 7.53s. One warning notes the isolated test process had no `SECRET_KEY`; no production configuration was used by this test command.

At initial preflight, the actual QA schema was checked read-only using its existing private configuration. Schema `cpa_agent_qa_20261024_01` was at `0021_agent_task_contract`; the profile-preference and two relation tables were absent. The runtime account lacked `ALTER`, `INDEX`, and `REFERENCES`, so migration was correctly withheld from that account and no grant was changed. The subsequent root-only migration is recorded below.

## Frontend verification

Remote refs were read through the existing `127.0.0.1:7897` proxy. `origin/main` is `837378d4346c714db7b4d6bc92353bd841a5fb9d`; `origin/codex/release-candidate-20261023` is `17f8bc4a8cc1a4a804c15f5b225057e826a7b0dd`. The published main was fetched and merged into the local integration branch after a conflict-free `git merge-tree` preview.

- `npm run type-check`: passed.
- `npm run build-only -- --outDir C:\Users\HoSheil\AppData\Local\Temp\cpa-xingtu-build-integrated-837-20261024`: passed; 4,372 modules transformed. Existing warnings: `lottie-web` uses `eval`, and some chunks exceed 500 kB.
- The initial integrated run had 84/85 pass because `tools/test-career-climbing.mjs` still imported `src/composables/careerClimbingContract.ts`, which graph commit 708bdd5 intentionally deleted.
- That test now exercises the current Vue graph contract instead of restoring the deleted module or dropping coverage. It checks the CareerReport graph integration, the `/api/career/relationship-graph` request, focus/compact filtering, role selection, and the truthful one-node/zero-edge empty state with synthetic fixtures.
- `node --test tools/test-*.mjs`: 229 passed, 0 failed.
- `npm run type-check`: passed.
- `npm run build-only -- --outDir C:\Users\HoSheil\AppData\Local\Temp\cpa-xingtu-build-career-final-20261024`: passed, 4,372 modules transformed. Existing warnings remain for `lottie-web` eval and chunks above 500 kB.
- These checks ran on the isolated integration worktree; no push, merge, or deployment was performed.

## Career source inventory (read-only)

The additional production inventory used the existing protected configuration in-process, asserted `localhost:13306/cpa_agent`, and returned aggregates only.

- Curated pathways: 1 active revision and no other pathway status rows. The active revision has 8 canonical skill rows; each has HTTPS source evidence and a non-empty note.
- The existing cold-start `career_insights` catalogue has 35 distinct roles and 192 core-skill items. All 192 items are plain strings, with no per-skill source evidence. Exact normalized name matching yields 101 candidate matches across 50 taxonomy IDs and 32 roles; these remain candidates, not publishable evidence.
- `career_job_fact` has 1 fact under the active revision with 7 requirement clauses, none linked to a canonical skill ID. It has a source URI, content hash, and fetched timestamp. `career_salary_reference` has 1 row for 1 revision.
- No reviewed, complete, multi-role source was found. The legacy `career_path` table is student-specific and was not read. These inputs support 1 graph node and 0 edges; the projection was not applied. No draft pathway was published and no role or edge was fabricated.

## QA backup and migration rehearsal outcome

The user explicitly approved using the existing local MySQL root identity only for this QA schema, without creating credentials or changing grants. Before the final approved DDL, the then-running QA API PID 55928 and QA worker PID 39964 were identity-checked, the worker's active/reserved/scheduled counts were zero, and the QA queue was empty. Only QA writers were stopped; production API/worker/Beat, production schema, Redis, Neo4j, tunnel, and other services were not stopped.

A private QA-only dump was created at `C:\Users\HoSheil\AppData\Local\CPA-Agent\qa\backups\pre-0022-23-20261024T194743Z\cpa_agent_qa_20261024_01-pre-0022-23.sql`. It contains 85 table definitions, is 525,216 bytes, and has SHA-256 `6bb14006e7319739d03a8ed2b6bae4489789790218d8f44f91a602b9be5373d7`. A table-count manifest was saved beside it; the backup directory ACL was verified to allow only `NEUTRONMBOOK\HoSheil`. The dump was made with the existing root identity loaded in-process; the credential was not printed, placed on the command line, or saved again.

The first formal reviewer attempt was rejected before execution with this reason: “该命令会使用生产配置中的 root 凭据在 QA 数据库执行不可自动降级的 0022/0023 DDL；用户要求涉及凭据及不可逆操作先确认，但没有可信的明确批准。” At that point no DDL had run. The user then explicitly reconfirmed that exact QA-only credential source and accepted the DDL rollback risk. After the new approval, one guarded retry succeeded; the final verified outcome is recorded below. The prior denial was not bypassed.

Before the new approval arrived, QA was restored to the pre-integration source to avoid leaving it down. Those baseline processes were later stopped only for the approved migration. The current QA API and worker now run the integration worktree; their verified state is recorded below.

The previously rejected DDL action is now complete under the user's materially new explicit approval. No credential was copied, no grant changed, and the production schema was not a target.

## Evidence-backed career source plan (not ingested)

Public primary-source review found a bounded first wave of three possible Chinese occupational records. The Ministry of Human Resources and Social Security's 2022 occupational classification lists codes and titles, but that list supplies identity, not skill evidence. OSTA's standards portal provides the detailed national occupational standards below. No source text has been copied into the database and no pathway has been activated.

| Candidate | Official identity / source | Use in first wave |
| --- | --- | --- |
| 计算机程序设计员 | `4-04-05-01`; OSTA National Occupational Skill Standard (2022 edition), promulgated by 人社厅发〔2022〕31号 | First priority; extract only competency clauses and section/page references. |
| 网络与信息安全管理员 | `4-04-04-02`; OSTA 2020 standard; the data-security administrator trade has a distinct 2024 standard/code `4-04-04-02-004`, promulgated by 人社厅发〔2024〕18号 | First priority for security/data-security competencies; keep the trade as a specialization under the parent occupation unless the current taxonomy explicitly models it separately. |
| 信息安全测试员（渗透测试员） | `4-04-04-04` in the 2022 occupational classification; OSTA national standard page lists the penetration-testing standard | Third candidate after validating the current standard edition and exact scope against the official standard. |

Official references: [2022 digital-occupation list on China Job / MOHRSS](https://chinajob.mohrss.gov.cn/h5/c/2022-10-28/363399.shtml); [OSTA 2022 Computer Programmer standard](https://www.osta.org.cn/api/sys/downloadFile/decrypt?fileName=4eNsZIOv3WFNDfbnldJHeA%2F2024%2F4%2F30%2F6ee3162cddee45459605cb3a449cb5da.pdf); [OSTA 2024 Network and Information Security Administrator (Data Security Administrator) standard](https://www.osta.org.cn/api/sys/downloadFile/decrypt?fileName=4eNsZIOv3WFNDfbnldJHeA%2F2024%2F4%2F29%2F7625e102680c4fdaa3b9c3984871bd22.pdf); [OSTA Information Security Tester (Penetration Tester) standard](https://www.osta.org.cn/api/sys/downloadFile/decrypt?fileName=4eNsZIOv3WFNDfbnldJHeA%2F2024%2F4%2F29%2Ff478fe1999b24e228d6eedfd362d4740.pdf). Sources reviewed 2026-10-24. The official occupational list identifies, among others, software engineering technicians (`2-02-10-03`), information-system analysis engineering technicians (`2-02-10-05`), data-analysis processing engineering technicians (`2-02-30-09`), big-data engineering technicians (`2-02-38-03`), and information security testers (`4-04-04-04`). Those engineering occupations remain discovery candidates until a suitable final standard with role-specific competencies is verified.

Proposed ingestion rules:

1. Create a staging source record keyed by the official occupation code, standard title/version, issuing notice, URL, retrieval date, and document hash if local retention is permitted. Keep standard text at the source; store a short paraphrase and exact section/page locator rather than mirroring the PDF. Public accessibility alone is not a redistribution license.
2. Separate task/competency statements from entry prerequisites, training hours, qualification conditions, and knowledge lists. Do not turn a course topic or a job-title string into a skill.
3. Map a source competency to an existing canonical `skill_id` only after a human reviews semantic equivalence. Store the source standard/version and clause locator for every role-skill fact. If no canonical skill fits, create a review candidate instead of publishing a new ID automatically. Do not use normalized name matches from the current cold-start catalogue as evidence.
4. Keep the first source-backed pair focused on programmer and network/data-security work; review the information-security-tester standard as the third record. A relationship edge is not pre-approved: calculate it only after at least two active pathways have comparable, reviewed skill sets and the same canonical skill is independently supported by each role's evidence. Zero shared skills means zero edges.
5. Public job descriptions may supplement standards only via a small manual sample from employer career pages or official employment services, after checking the page's reuse terms. Record employer, posting ID/title, date/location, URL, retrieval date, and brief evidence locator. No bulk scraping, paid source, model-generated skill, or private resume is in scope.

With current production source data, the real projection remains one node and zero edges. The source plan above does not alter or fill the production catalogue. It proposes a reviewable first batch; it does not claim that any new role or relationship has been ingested.

## Not completed

- QA-only migration rehearsal is now complete: `cpa_agent_qa_20261024_01` is at `0023_career_relation_projection`; the pre-migration 85 tables have zero row-count differences, and the three added tables are empty. The profile API was exercised through the integration Flask app with the QA Redis-backed session and real QA MySQL; profile save/readback/stale-version rejection/restore passed. No production schema or account was touched.
- No relation projection write. The current source has fewer than two active pathways and produces zero edges.
- No production data, services, worker, DNS, or remote refs were changed. Neither integration branch was pushed or merged remotely. Production API health remained `ok` on `127.0.0.1:5001`.
- No model-provider calls were made during this follow-up; the shared budget remains exhausted at 20/20. Two earlier provider connection attempts failed before a response; provider-side billing is unknown.
- No real-source graph apply was made because only one active curated pathway exists. Additional reviewed, evidence-backed pathway materials are needed before a useful relation graph can be computed.

Before any production release, take a fresh protected backup for the current schema. The previously recorded backup predates migration 0021 and is not a valid pre-0022/0023 recovery point. MySQL DDL has no automatic downgrade; rollback must retain additive schema or use an explicitly reviewed restore procedure.

## QA migration and runtime completion (2026-10-25 host local time)

After the user explicitly reconfirmed the exact QA-only root-credential use and accepted the additive DDL rollback risk, the approved operation was retried once. The integration worktree reached a technical preflight failure before DDL because its temporary checkout has no project `.env`; no provider key was copied into the QA file. A guarded process-only startup then loaded the already-approved Qwen settings from the existing project configuration into memory, retained the QA-only MySQL/Redis/Neo4j targets, and did not issue a model request. Migration execution used the existing protected root configuration in process only, asserted the schema name before connecting, and ran only the explicit 0022 then 0023 revisions.

- Fresh QA dump: `C:\Users\HoSheil\AppData\Local\CPA-Agent\qa\backups\pre-0022-23-20261024T194743Z\cpa_agent_qa_20261024_01-pre-0022-23.sql`; 525,216 bytes; SHA-256 `6bb14006e7319739d03a8ed2b6bae4489789790218d8f44f91a602b9be5373d7`. Its table-count manifest matched the pre-migration QA state before execution.
- Migration result: Alembic `0023_career_relation_projection`; 88 base tables. `user_account.profile_revision` exists. The new notification and relation tables exist with zero rows. The profile FK column collation matches `user_account.id` (`utf8mb4_0900_ai_ci`); expected FKs, unique/check constraints and relation indexes are present. All 85 pre-existing table counts match the backup manifest exactly; no rows were lost or added by migration.
- Integration QA API PID 64828 is listening only on `127.0.0.1:5003`; `/health` returned `ok`. Logs and uploads use subdirectories under the ACL-protected QA directory. Integrated QA worker PID 40500 responds as `celery@NeuTronMbook`, subscribed only to `cpa.agent.qa.20261024_01`; active/reserved/scheduled and queue depth are all zero. Production API remained healthy on port 5001; production worker/Beat and SSH tunnel were not changed.
- Live QA app acceptance: authenticated with an existing synthetic QA account through a Redis-backed session; `GET /api/auth/profile` 200, `PATCH` 200, DB-backed readback 200, stale revision 409, and restoration of the original synthetic signature 200. Revision advanced 0→2 as expected. `GET /api/auth/notification-preferences` returned 200/unconfigured without inserting a preference. `/api/career/relationship-graph` returned 200 with an empty graph (0 nodes/0 edges) because this QA schema contains no active pathway material.
- Targeted command, run from the integration worktree using the `mkg` Python 3.10 environment:

  ```powershell
  D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m pytest -p no:cacheprovider --basetemp C:\Users\HoSheil\AppData\Local\Temp\cpa-agent-integration-tests-20261024 backend/tests/test_account_profile_api.py backend/tests/test_account_profile_migration_collation.py backend/tests/test_career_relations.py backend/tests/test_career_relation_migration_chain.py -q
  ```

  Result: 18 passed, one warning for absent test-only `SECRET_KEY` in isolated pytest configuration. No model calls were made; the shared QA request counter remains 20/20. Earlier failed provider connection attempts had no response; provider-side billing remains unknown.

The API profile save/restore test used a synthetic QA account only. The profile revision metadata is intentionally incremented by its successful writes; content was restored. No notification row, relation run, or edge was inserted. Graph calculation/apply remains unperformed because the source inventory still has one active production pathway and cannot support a genuine inter-role edge. The integrated backend and frontend worktrees remain local-only; `.git` index permissions prevent safely committing this report from the current environment.
