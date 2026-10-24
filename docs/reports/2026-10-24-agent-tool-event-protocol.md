# Agent tool-event protocol verification — 2026-10-24

## Scope

Implemented in the isolated `codex/tool-event-protocol-20261024` worktree, based on backend search commit `83efd99e937e0cdf0ff05a4d877de302df619c69`. The change connects durable worker tool calls to the existing owner-bound `agent_run_event` stream. It adds no migration or database table and does not change production processes.

Each real LangGraph tool call now has `run.tool_call_started` and `run.tool_call_settled` events. They share a stable `turn_id`, a server-generated `assistant_message_id`, and the provider `tool_call_id`; payloads also bind `plan_version`, `contract_revision`, `step_index`, and `attempt`. The start is persisted before ToolNode execution. The settled event is persisted only after a matching `ToolMessage` is returned, and that ToolMessage is then passed into the next model iteration. A completed run carries a stable final `assistant_message_id` in its `run.completed` event.

Events store only the allowlisted tool name, status, IDs, argument-name/count summary, output shape/size/count summary, and constrained error code. Argument values, tool result text, prompt text, and model reasoning are not persisted in these new event payloads. Event writes verify owner, run, session, active worker claim, plan/revision, step attempt, and the server tool allowlist. Matching retries return the same event sequence; mismatched identity or content is rejected. The existing `/api/agent/runs/<run_id>/events?after_seq=…&limit=…` response exposes the typed payload under the existing cursor and owner boundary. Historical event types remain readable.

## Verification

Command, from the repository root with the documented `mkg` Python 3.10 environment:

```powershell
D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m pytest backend/tests/test_agent_run_executor.py backend/tests/test_agent_run_store.py backend/tests/test_scoped_tool_failure_contract.py backend/tests/test_agent_runs_api.py -q --tb=short
```

Result: **93 passed, 2 warnings** in 18.68 seconds. Tests use FakeLLM, a real LangGraph `ToolNode`, Flask's test client, and SQLite in-memory persistence fixtures. They cover successful and failed tool execution, `ToolMessage` reinjection, persisted event ordering and cursor replay through the HTTP route, owner/session/claim/step boundaries, sensitive-value exclusion, final answer ID association, failure of the start-event write before tool execution, and refusal to retry through the graph fallback after durable execution failure. Warnings were the expected unset test `SECRET_KEY` warning and the installed `pynvml` deprecation warning.

No provider request, real MySQL/Redis/Neo4j access, production database write, service restart, or browser replay was performed. The front end may continue to ignore these new event types until its event renderer is adapted; the HTTP contract is now available for that separate consumer change. A green fixture suite does not prove a live production worker/UI round trip.

## Reference and adaptation boundary

Reviewed the local DeepSeek Harness snapshot `d347e703908d0406b7a7ef80e3a0e594d86b2215` (`0.1.3-alpha.1`, MIT), especially `packages/core/agent-loop/src/agent.ts`, `tool-calls.ts`, and `packages/llm/llm-deepseek/src/translate.ts`; and OpenCode commit `388406238bd5ca15564a762840a2362c3a45bd9c` (MIT), typed session message/event handling. Adapted only structured tool-call/result association and ordered replay. CPA keeps its own owner isolation, read-only durable-run allowlist, worker-claim fence, capability checks, and call budget; no reference runtime code was copied.
