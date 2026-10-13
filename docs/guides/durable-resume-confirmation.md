# Durable L0 resume confirmation

## Reuse and boundary

- `langgraph-checkpoint-mysql[pymysql]==3.0.0`: reuse `PyMySQLSaver`, `StateGraph`, `interrupt`, and `Command(resume=...)`; no custom checkpoint serializer or parallel suspension state machine.
- Inspected `opencode` commit `388406238bd5ca15564a762840a2362c3a45bd9c`, `packages/opencode/src/session/{processor,message-v2,prompt}.ts`: interrupted tool state must not masquerade as successful completion.
- Inspected Codex commit `a06545b311fe01e51ce855c7aa5d8da21e9e7aaf`, `codex-rs/core/src/client.rs` and `client_common.rs`: transport cancellation is not domain confirmation. These TypeScript/Rust loops are not transplanted into the Python backend.

The production Agent's `pipeline_run` L0 tool starts this workflow after the candidate is persisted. `POST /api/profile/student/confirm` restores it using the Principal-derived student ID. Page-originated candidates lazily initialize the same graph. The workflow stops at confirmed student profile: it does **not** resume the whole Supervisor/Experts turn or automatically authorize L2.

The thread is a SHA-256 digest of owner and candidate task ID, not a user-supplied chat thread. Every call checks the authoritative task owner before looking up checkpoints. A MySQL advisory lock serializes start/resume across processes. The existing transactional `confirm_resume_candidate` is the sole business writer; retry after a crash between domain commit and graph checkpoint remains idempotent. Completed resumes re-read the domain record and reject superseded confirmations. Missing MySQL never falls back to memory for this workflow.

`confirmation_workflow.resume_state` describes only this workflow. `TurnOutcome.resume_state` remains `not_available`, since whole-turn recovery has not been established.

## Verification

Run from repository root with MySQL dependencies running:

```powershell
$env:PYTHONUTF8='1'
$env:CPA_TEST_PIPELINE_MYSQL='1'
& D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m pytest backend/tests/test_resume_confirmation_mysql.py -q
```

The test creates an explicitly synthetic candidate, starts a production workflow, exits that Python process, denies another owner, resumes through the production Flask confirmation route in a fresh process, retries in another process, and re-reads the actual confirmed profile. Principal injection replaces login only; business writes, checkpoints, HTTP controller and database reads are real. A second case reproduces the crash window by committing the domain confirmation while leaving the graph interrupted, then verifies replay preserves the original confirmation timestamp and record. It cleans only its uniquely named task/profile/checkpoints/events. It does not claim OCR quality or real résumé extraction accuracy.
