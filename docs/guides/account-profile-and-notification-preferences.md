# Account Profile and Notification Preference Contract

The authenticated account center reads identity facts from the signed-in `user_account` row. The profile route is scoped by the server-side session principal; request bodies cannot select another account.

## Routes

- `GET /api/auth/profile` returns `id`, `username`, `name`, `email`, `phone`, `signature`, `role`, `status`, `created_at`, `last_login_at`, `auth_method`, and a profile `revision`.
- `PATCH /api/auth/profile` accepts only `name`, `email`, `phone`, and `signature`, plus `expected_revision`. It validates and trims values, increments the revision in a transaction, and returns the row read back from MySQL. A stale revision returns `409`; an unavailable/unmigrated schema returns `503` without claiming success.
- `GET /api/auth/notification-preferences` returns nullable per-user intent, a revision, and each channel's actual availability.
- `PUT /api/auth/notification-preferences` stores `true`, `false`, or `null` intent with optimistic concurrency. It does not send messages, subscribe a provider, or enable a delivery channel.

The server-authenticated identity currently uses the platform username/password login. `auth_method` is derived from the account row's required `password_hash` presence; the hash itself is never returned. The account schema has no external-provider link table, so the UI labels this as a login method rather than claiming a separate email/provider binding. The available facts do not include a password-change timestamp, second-factor enrollment, or a trustworthy list of active sessions; the UI must show those as unavailable instead of inventing them. The existing password-change route is the only supported security action and invalidates prior sessions.

Email, SMS, and preference-driven system notification delivery are not wired. The account center may save preference intent, but these flags have no sender or subscription side effect.

## Schema

Migration `0022_account_profile_preferences` adds a default-zero `user_account.profile_revision` and a per-user `user_account_notification_preference` row. The migration is additive and does not rewrite or delete existing profiles. Its `downgrade()` intentionally refuses to drop profile revisions or user preference data. Apply it through the project's normal deployment migration process before enabling these routes; do not run it against production as part of a code review or UI task.
