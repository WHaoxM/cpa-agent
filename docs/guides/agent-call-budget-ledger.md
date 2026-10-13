# Agent model-call budget slice (2026-10-09)

## Reuse and boundary

- Local references checked: OpenCode `388406238b`, `packages/opencode/src/session/processor.ts` records provider `usage` on every step and `session/compaction.ts` treats the model window as a measured constraint; Codex `a06545b`, `codex-rs/core/src/client.rs` carries response `token_usage` through completion events. These are design references, not copied code.
- Implemented with installed LangChain/LangGraph `Runnable` and `convert_to_openai_messages`/`convert_to_openai_tool` for the final model-call boundary. The existing `ContextWindowStatus` estimator remains the preflight authority; actual provider `usage_metadata` / OpenAI SDK `usage` remains the accounting authority. No second tokenization framework was introduced.
- Scope is synchronous `ModeRuntime.chat` calls through `LLMClient` and `_build_llm_with_tools`, including router, Experts Lead, scoped/subagent calls, and LangGraph tool rounds reached through those wrappers. The request-local `usage_ledger` lists each attempted call with ID, stage, provider/model, final-message preflight, usage, and finish reason. The public turn projects only safe accounting fields; an empty ledger has `verified=false`. `CPA_AGENT_TURN_TOKEN_CAP` controls the turn cap (default 30000); pre-dispatch reservation includes estimated **input plus maximum output**, and provider usage replaces the reservation after completion. Unknown model window/input estimate, missing provider usage, truncated output, and exhausted cap do not permit a further model dispatch. A dispatched transport error is recorded with unknown usage and blocks failover within that turn. OpenAI SDK and `ChatOpenAI` implicit retries are disabled (`max_retries=0`) because otherwise a single wrapper call may hide multiple charged attempts.

## Verify

```powershell
[Console]::OutputEncoding=[System.Text.Encoding]::UTF8
$OutputEncoding=[System.Text.Encoding]::UTF8
$env:PYTHONUTF8='1'
& 'D:\Miniconda\miniconda3\envs\mkg\python.exe' -B -m pytest backend/tests/test_agent_call_budget_ledger.py backend/tests/test_model_context_budget.py backend/tests/test_platform_acceptance_baseline.py -q
```

The synthetic test proves that a configured-but-unknown LangGraph model never reaches `.invoke`, that two tool rounds have two separate usage entries, and that turn-cap/missing-usage/failed-dispatch cases fail closed. A narrow real `.env`-configured `qwen-plus` request on 2026-10-10 returned `prompt_tokens=11`, `completion_tokens=1`, `total_tokens=12`, `finish_reason=stop`; the ledger contained one call and `fits_estimate` preflight. No prompt, API key, or response text was saved. This proves this provider boundary, **not** a complete Agent/Experts business request or career-advice quality.

## Remaining limits

- This is not a full platform ledger: model-backed `rollup_summary` jobs now persist independent accounting as described below; reflection/audit still use their existing transport and logs. Legacy/demo engines, downstream OCR/provider costs, and model calls made outside `LLMClient`/LangGraph are not captured. `usage_ledger` on an otherwise empty/mock turn is not evidence of zero tokens.
- An HTTP-level provider failure may have consumed tokens without returning `usage`; the call is now marked unverified and the turn stops instead of treating it as free or failing over. Its exact cost cannot be reconstructed. The existing `last_usage` field is compatibility diagnostics, not the aggregate.
- `ContextWindowStatus` is an estimate, not provider tokenizer parity. Tool schema is included in LangGraph preflight; unsupported multimodal blocks fail closed. Actual provider usage and a clean full-chain trace are still required before claiming the Agent/Experts token budget accepted.

## Background summary accounting (2026-10-10)

For backend maintainers: deploy `backend/scripts/mysql/migration_agent_fork_usage.sql`
before enabling model-backed summary work. The additive table uses the existing
SQLAlchemy/MySQL client; no runtime DDL, additional queue, tokenizer, or retry
framework was introduced. Reuse references were rechecked at full revisions
OpenCode `388406238bd5ca15564a762840a2362c3a45bd9c`
(`session/processor.ts`, per-step provider usage) and Codex
`a06545b311fe01e51ce855c7aa5d8da21e9e7aaf`
(`codex-rs/core/src/client.rs`, session/turn correlation and completed token usage).
Their TypeScript Effect/Rust runtimes cannot be imported into this Flask service;
the adaptation is limited to accounting at the existing Python provider boundary.

`ModeRuntime.chat` allocates an accounting `usage_turn_id` (not a resumable Agent
turn identifier). `usage_ledger.background_jobs` exposes job IDs and dispatch-time
status. These snapshots do not become live completion notifications. The durable
row is scoped by job ID, owner student, session and origin usage turn. Internal
`fork_usage_store.read_record` requires all four keys; it is not a public API and
callers must authenticate the owner first. No prompt or response is persisted.

`CPA_AGENT_FORK_TOKEN_CAP` optionally overrides the separate fork cap; otherwise
the existing turn cap/default applies. This is **not** a shared parent+child cap.
Failure to persist the queued record skips model dispatch and exposes
`accounting_unavailable`. A budget stop, unknown provider usage, or truncated
summary sets `partial`, leaves the source summary cursor unchanged, and records
`retry_on_next_turn`: a later eligible chat creates a new job and reprocesses the
unconsumed source messages. It does not resume an arbitrary tool or Agent plan.
Do not manually replay a job whose state is `running`: process loss or final
database-write failure can leave unknown charged usage. The daemon-thread
transport remains non-durable; queued/running records after restart are not
proof of completion, nor automatically redelivered.

Focused verification:
`D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m pytest backend/tests/test_fork_usage_accounting.py backend/tests/test_agent_call_budget_ledger.py -q`.
The test drives the real summary handler and LLMClient, uses synthetic provider
responses and a file-backed SQLite SQLAlchemy adapter, verifies blocked dispatch,
preserved cursor, later retry, per-call usage, and cross-owner/turn lookup denial.
It does not establish MySQL production deployment, full HTTP Experts execution,
OCR accounting, or crash-safe execution.
