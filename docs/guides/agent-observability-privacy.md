# Agent observability privacy boundary (2026-10-10)

## Finding and repair

The default Supervisor request path is `POST /api/agent/chat` →
`_run_supervisor` → `SupervisorService.chat` → `ModeRuntime.chat` →
`LLMClient.chat_with_metadata`.  When Langfuse credentials were configured,
`LLMClient` passed the final user-message preview to
`llmops.trace_generation`; that hook sent up to 2,000 input characters and up
to 4,000 response characters to the configured Langfuse host.  In this
application those strings can contain a resume, contact information, or
owner-scoped evidence.  They are not needed to account for provider/model,
finish state, or provider-reported token usage.

`trace_generation` now exports only trace name plus supplied accounting
metadata by default.  It sends prompt and answer content only when the deploy
operator explicitly sets `CPA_LANGFUSE_CAPTURE_CONTENT=1` (also accepts
`true`, `yes`, or `on`).  This is an observability-export switch, not a user
consent mechanism: do not enable it for production student data unless the
deployment's data-governance process permits that external content export.
The change leaves `LLMClient`'s request-local `usage_ledger` and provider
usage collection intact; absence of content in Langfuse is not absence of
usage data.

There are no separate Langfuse update/error calls in the current hook:
creation is `client.trace(...)` then `trace.generation(...)`, and every normal
or exceptional body exit reaches `generation.end(...)` in `finally`.  In the
default mode none of those calls receives `input`, `output`, prompt preview,
or answer text.  A provider exception therefore ends the trace with only the
already-safe metadata, rather than accidentally restoring the old preview.

## Reuse review and adaptation boundary

- Local OpenCode checkout `388406238bd5ca15564a762840a2362c3a45bd9c`:
  `packages/opencode/src/session/processor.ts` records structured per-step
  token/cost fields, while `packages/opencode/src/cli/cmd/export.ts` replaces
  exported file text, URI, and patches with redacted placeholders.  This fix
  follows the latter boundary for optional third-party telemetry without
  importing its TypeScript/Effect runtime.
- Local Codex checkout `a06545b311fe01e51ce855c7aa5d8da21e9e7aaf`:
  `codex-rs/core/src/client.rs` handles `ResponseEvent::Completed` by
  propagating `token_usage` to telemetry and completion state.  CPA-Agent
  retains the existing provider-reported usage ledger; it does not copy the
  Rust client or add a second telemetry framework.

## Verify

```powershell
[Console]::OutputEncoding=[System.Text.Encoding]::UTF8
$OutputEncoding=[System.Text.Encoding]::UTF8
$env:PYTHONUTF8='1'
& 'D:\Miniconda\miniconda3\envs\mkg\python.exe' -B -m pytest `
  backend/tests/test_llmops_privacy.py `
  backend/tests/test_agent_call_budget_ledger.py -q
```

The fake Langfuse regression uses resume-like PII.  With only Langfuse keys,
the fake SDK receives no `input` or `output` fields on a successful `LLMClient`
request **or a provider-error exit**, but retains structured token usage metadata
on success.  With the named opt-in it receives the supplied content.  This
proves the Python hook's payload boundary; it does not prove a remote Langfuse
retention policy, user consent, or full Agent quality evaluation.
