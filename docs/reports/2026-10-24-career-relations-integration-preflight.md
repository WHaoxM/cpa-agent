# Career relation integration preflight — 2026-10-24

## Scope and result

This report records local integration and read-only database validation for the account profile and career relation modules. No database migration, graph projection write, production service restart, remote push, or deployment was performed.

The backend integration worktree is `codex/integration-prep-20261024`. At code-verification time it was at `a97a947196fe06eb6cb58daec34d5f65a2a6aa90`; a documentation-only commit `17be8c8` was added afterward. The branch contains the same-session history fix `336e8b3dcc856c3f93573c5e25eb247854478208`, account profile backend `f2422ccb565d815403b733db213e207ad5a3a1c4`, career relation backend `1a454340057921b37f7b752237e55cc5b3cab7bc`, migration draft `0023_career_relation_projection` (`f6ab1fd5ae264344d2f9de81a101d87728af7b29`), and the profile-FK collation correction in `a97a947`. The worktree was clean after the documentation commit.

The frontend integration worktree is `codex/integration-prep-20261024` at `a034e6271d5a1fac6369a927087d578b577086c7`. It contains the published `main` commit `837378d4346c714db7b4d6bc92353bd841a5fb9d` as an ancestor, candidate UI fix `17f8bc4a8cc1a4a804c15f5b225057e826a7b0dd`, profile frontend `2212a11f96013ab10e7dcf02720fa47ce024f308`, and career graph frontend `708bdd5c99b143fee5a431a1340670c6b5dc2311`. The integrated branch was not pushed.

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

The actual QA schema was checked read-only using its existing private configuration. Schema `cpa_agent_qa_20261024_01` is at `0021_agent_task_contract`; the profile-preference and two relation tables are absent. QA API port 5003 remains actively owned by Python PID 54396 and uses this schema. Applying 0022/0023 there would mutate a live QA service's database, so the rehearsal was not run pending coordination of a QA pause or a separate already-authorized scratch target. No new grant or credential was created.

## Frontend verification

Remote refs were read through the existing `127.0.0.1:7897` proxy. `origin/main` is `837378d4346c714db7b4d6bc92353bd841a5fb9d`; `origin/codex/release-candidate-20261023` is `17f8bc4a8cc1a4a804c15f5b225057e826a7b0dd`. The published main was fetched and merged into the local integration branch after a conflict-free `git merge-tree` preview.

- `npm run type-check`: passed.
- `npm run build-only -- --outDir C:\Users\HoSheil\AppData\Local\Temp\cpa-xingtu-build-integrated-837-20261024`: passed; 4,372 modules transformed. Existing warnings: `lottie-web` uses `eval`, and some chunks exceed 500 kB.
- Ten selected Node test files ran 85 tests: 84 passed, 1 failed. The failure is an integration mismatch: `tools/test-career-climbing.mjs` still reads `src/composables/careerClimbingContract.ts`, which the graph commit 708bdd5 intentionally deleted. The failure is recorded, not hidden by restoring dead code or deleting the test.

## Not completed

- No QA or production application of 0022/0023; no live MySQL profile API test.
- No relation projection write. The current source has fewer than two active pathways and produces zero edges.
- No production data, services, worker, DNS, or remote refs were changed. Neither integration branch was pushed or merged remotely.
- No model-provider calls were made; the shared budget remains exhausted at 20/20.
- The frontend integration still needs the career-climbing test contract reconciled before it can be called fully passing.

Before any production release, take a fresh protected backup for the current schema. The previously recorded backup predates migration 0021 and is not a valid pre-0022/0023 recovery point. MySQL DDL has no automatic downgrade; rollback must retain additive schema or use an explicitly reviewed restore procedure.
