# Agent Turn And Artifact Contract

## Scope

This spec covers the single-user Agent chat loop contract:

- one `PublicTurn` shape for sync HTTP and SSE;
- durable `MessagePart[]` history replay;
- owner-scoped `ArtifactRef` cards for typed domain artifacts.

It does not merge `/api/messages` inbox semantics into Agent conversations.

## PublicTurn

`POST /api/agent/chat` returns the legacy fields (`reply`, `tool_calls`, `thinking_steps`) plus:

- `turn_id`: opaque id for this assistant turn;
- `parts`: replayable message blocks;
- `artifacts`: public artifact references committed during the turn;
- `events`: the same typed events used by `/api/agent/chat/stream`.

SSE emits the `events` list as typed Server-Sent Events. The first implementation still folds a completed runtime result into events, but JSON and SSE now share one contract.

## Message Parts

Supported part types:

- `text`
- `progress`
- `tool_call`
- `attachment`
- `artifact_ref`
- `upgrade_hint`
- `notice`

History stores these as `parts_v1`; old rows without parts are adapted to one `text` part.

## Artifacts

Artifacts are references, not payload dumps. `agent_artifact` stores:

- owner: `student_id`;
- conversation pointers: `session_id`, `turn_id`;
- public shape: `kind`, `status`, `title`, `summary`, `preview`, `actions`, `version`;
- domain pointer: `resource_type`, `resource_id`.

Full portrait, match, report, plan, file, or pipeline data remains in its authority store. Unknown artifact kinds must render as a safe fallback card.

## Completion Rule

A golden-chain step is business-complete only when a durable resource or task exists and an artifact ref has been signed. Blackboard summaries and model prose are not sufficient completion evidence.
