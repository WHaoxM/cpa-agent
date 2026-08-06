# ADR-0003: RequestScope Runtime Isolation

Date: 2026-07-21

## Status

Accepted

## Context

Agent runtime APIs previously accepted `student_id`, `user_id`, `session_id`, and
`file_id` directly from request body/query parameters. That made sessions,
memory, uploaded files, mounted resources, and some user data endpoints depend on
client-provided ownership claims.

The highest-risk surface was not prompt injection. It was direct object access:
a caller could ask for another session, mount another user's file, or invoke a
tool with nested identity parameters unless every individual endpoint remembered
to re-check ownership.

## Decision

Introduce a server-resolved `Principal` and `RequestScope` as the trusted
identity contract for user-owned resources.

- In demo mode, all user resources bind to a configured server-side demo
  principal (`CPA_DEMO_STUDENT_ID`, default `student_001`). Client-supplied
  identity fields are ignored.
- In strict mode (`CPA_DEMO_MODE=false`), user resource APIs fail closed with
  401 unless an authentication adapter provides a principal through Flask `g`.
- Session, memory, file, mount, voice, profile, favorites, and messages routes
  must compare resource owner to `RequestScope.student_id`.
- Public session context updates may only touch explicitly whitelisted UI keys;
  blackboard writes remain internal runtime operations.
- Tool calls pass through `ToolScopeNormalizer`, which overwrites top-level and
  nested identity/session parameters before execution.
- Legacy `langgraph_legacy` and `react` engines are disabled by default and only
  available behind an explicit demo development switch.

## Consequences

This breaks the old convenience path where frontends could choose arbitrary
`student_id` values in body/query parameters. Local demos remain runnable because
demo mode provides a fixed identity. Production-like strict runs now need a real
auth adapter before user-owned API routes are usable.

The trade-off is intentional: resource ownership becomes a server invariant
instead of a frontend convention.
