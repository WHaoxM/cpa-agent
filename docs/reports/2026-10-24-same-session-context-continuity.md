# Same-Session Context Continuity — 2026-10-24

## Finding

The observed failure was inside one conversation, not a cross-session memory lookup. A synthetic QA conversation contained the same four messages in Redis and the MySQL `conversation_message` projection, with matching `message_id` values. The old history fallback ran whenever the Redis list held fewer than 16 messages, reversed the MySQL rows, and prepended them to Redis without reconciling identity. That produced eight entries in the wrong order from four actual messages.

A prior real-model replay also showed the assistant failing to recall a synthetic fact from the immediately preceding turn. The final provider HTTP request body was not captured in that replay, so the bad server-side history projection is confirmed, but it is not possible to prove from that run exactly what the provider received or that this was the only cause of its answer.

## Change

- `SessionManager.get_history_for_agent()` now asks MySQL only for the latest bounded page when the cached session history cannot fill the 16-message window.
- Pure reconciliation lives in `backend/app/services/session_history.py`. It merges by stable `message_id`, preserves distinct messages even when their text repeats, uses MySQL row ID order for equal timestamps, and falls back to Redis order when overlap or interleaving cannot be established safely.
- The storage adapter still owns owner/session-bound retrieval and projection. Ask context assembly separately removes a duplicated current message, filters system/tool protocol roles from ordinary conversation history, and passes the current user message once.
- The agent guide now maps the session history, reconciliation, context assembly, Ask runtime, and provider adapter boundaries, including where to debug missing history, missing attachment reads, tool denial, and stale task status.

## Validation performed

Runtime: the repository-authoritative `mkg` Python 3.10 environment. The two HTTP journey tests required a formal host execution because the workspace sandbox rejected loopback socket binding before assertions.

Initial command (the final rerun used the same arguments with `--basetemp C:\Users\HoSheil\AppData\Local\Temp\cpa-agent-history-final3-20261024`):

```powershell
D:\Miniconda\miniconda3\envs\mkg\python.exe -B -m pytest --basetemp C:\Users\HoSheil\AppData\Local\Temp\cpa-agent-history-final3-20261024 backend/tests/test_ask_history_lookup.py backend/tests/test_ask_offline_long_session_acceptance.py backend/tests/test_agent_turn_contract.py backend/tests/test_context_assembler.py backend/tests/test_session_turn_coordination.py backend/tests/test_agent_http_journeys.py::test_http_multi_turn_preserves_session_history_and_context backend/tests/test_agent_http_journeys.py::test_http_followup_keeps_prior_session_attachment_addressable_without_resending -q --tb=short
```

Final rerun after removing a duplicate test definition and adding the nonpositive-window regression: **52 passed in 18.05s**. The HTTP fixtures use a deterministic Supervisor and fake Redis/MySQL adapters; they establish the route/session/history boundary, not real database durability or model quality. A deterministic adapter test additionally captures the synthetic system/user/assistant/current-user role sequence passed to a fake `LLMClient`, verifies the prior synthetic fact is present, and confirms internal metadata is excluded. This is not a capture of an external provider's HTTP payload.

During test development, an initial adapter-test fixture accidentally supplied an empty EvidencePack, which routed into `scoped_invoke`. Two configured provider candidates returned connection errors before any response. No successful model response or token usage was returned; whether either failed connection attempt incurred provider-side accounting could not be verified. The fixture now marks the synthetic evidence as not needed and guards against tool invocation. No further model calls were made.

## Remaining limits

- Real-model request budget was already exhausted at 20/20. No post-fix live-model recall claim is made.
- The exact provider wire body from the earlier failed replay remains unavailable. Local tests now prove the history projection and deterministic model-adapter input shape, not what the prior production request contained.
- Ordinary Ask history is a bounded recent window (up to 16 user/assistant messages). A structured summary and opt-in memory digest are separate context sources; this does not amount to a complete, always-on long-term memory system.
- No production process, database, migration, or deployed artifact was changed by this history fix.

For code entry points and a synthetic replay command, see [Agent HTTP acceptance and source navigation](../guides/agent-http-acceptance.md).
