# ADR-0007: MySQL Account Epoch for All-Session Revocation

Date: 2026-10-13

## Status

Accepted

## Context

`Flask-Login` reloads role/status from MySQL on each request and `Flask-Session`
holds sessions in Redis. A disabled account is denied while disabled, but two
browsers that stay idle throughout disable → re-enable retain valid cookies.
The live HTTP/MySQL/Redis red test returned 200 for such an old cookie.
Deleting the cookie of one active request is not an all-device revocation.

## Decision

- `user_account.auth_epoch BIGINT UNSIGNED NOT NULL DEFAULT 0` is the account's
  revocation generation. Login stores the current generation in its Redis
  session. Every authenticated request must see an active DB account and an
  exactly matching session generation; legacy sessions lacking it fail closed.
- A MySQL `BEFORE UPDATE` trigger increments the generation on a real change
  to `status`, `role`, or `password_hash`. Unrelated updates preserve it.
  The trigger lives at the authoritative table boundary so Flask routes,
  SQLAlchemy, admin CLI, and direct SQL UPDATE writers cannot omit the bump. No
  separate cached role/status authority is permitted.
- Schema is additive and explicitly migrated. The app checks both column and
  trigger metadata at process startup. Absent schema, unavailable MySQL, or
  missing trigger metadata means account auth 503, not stale-cookie acceptance
  or a development-header fallback. After migration, restart all workers.
- A disable/re-enable cycle **does not** restore any old login. The user must
  authenticate again. Redis session keys are not immediately purged, but
  cannot authorize and expire under the configured session lifetime.

## Alternatives rejected

- Clear only the current request's session: idle devices are untouched.
- Enumerate/delete all Redis sessions by user: Flask-Session does not maintain
  a trustworthy user→session index; adding one creates a second consistency
  problem and can miss old sessions.
- Bump only in one HTTP admin route: direct DB/ORM/CLI writers could skip it.
- Use `updated_at`: profile edits would revoke unnecessarily, and timestamp
  precision/same-value updates cannot represent a reliable monotonic epoch.

## Migration and recovery

Fresh Docker volumes load `init_mysql.sql` (column) then
`init_account_session_epoch_trigger.sql` (trigger). Existing volumes must run
`flask --app app migrate-account-session-epoch`, an idempotent additive command;
`init_mysql.sql` alone never replays on an existing volume. The legacy
`init_mysql.py` semicolon splitter cannot parse trigger bodies, so its users
also run the command. A partial column-only migration leaves auth at 503;
rerun the command and restart. A conflicting trigger is not replaced silently.
The safe rollback is to hold account traffic closed while restoring a version
that checks the epoch; reverting to an old implementation that ignores it is
not a security-equivalent rollback. Do not drop the column/trigger during
ordinary code rollback.

## Consequences and limits

This adds one DB column and one trigger and requires metadata visibility for
the app DB user. Each authenticated request already reloads its account row;
the comparison adds no extra per-request DB query. A request admitted before a
concurrent disable commits may finish; requests starting after commit are
denied. The live test proves two idle cookies are both rejected after
disable→re-enable on one MySQL primary and Redis, not all production nodes or
cross-region replica behavior.
Deleting/recreating the same account ID or SQL `REPLACE` is a separate account
lifecycle operation not covered by the UPDATE trigger and must not be used as
an implicit re-enable path.
