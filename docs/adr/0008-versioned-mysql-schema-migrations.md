# ADR 0008: Versioned MySQL schema migrations

Status: accepted for the current local deployment path (2026-10-14).

## Context

The MySQL Docker entrypoint only executes `init_mysql.sql` on a new volume. Existing volumes accumulated separate `migration_*.sql` files, Flask/CLI `--install` commands, and some runtime schema ensures; editing the initialization script did not upgrade them. There was no common revision ledger, so deployment could report a running app while a required column or trigger was absent.

## Decision

Use [Alembic](https://alembic.sqlalchemy.org/en/latest/tutorial.html), already available through the SQLAlchemy/LangGraph environment, as the explicit MySQL **schema** revision ledger. The first revision freezes the current initialization table snapshot by normalized-content checksum and creates missing tables; following revisions adopt known account/source and runtime journal/projection additions. Operators run `upgrade head` before restarting writers, on both fresh and existing volumes, and verify the version plus the relevant business route/state. Future schema changes get new revisions, not edits to initialization or historical migration files. No migration runs implicitly in an HTTP request or startup as part of this decision.

Do not `stamp head` to bypass DDL. No automatic destructive downgrade or backfill is implied. MySQL DDL auto-commits, so migrations need safe retry boundaries and backups. Seed data, Neo4j/Milvus, LangGraph checkpoint tables, and uploaded files are separate concerns. Legacy runtime table-ensure code remains for compatibility and should be removed by later focused changes; a ledger version alone does not establish that every historical table has identical columns/indexes.

## Alternatives and trade-off

Continuing hand-run SQL would preserve the smallest immediate diff but repeats the existing no-ledger failure mode. Replacing all historical schemas with ORM autogeneration would introduce a second inferred source of truth and is risky for SQL triggers, hand-written JSON columns, and live volume drift. Alembic's explicit revisions reuse the installed SQLAlchemy ecosystem and make history/replay visible, while retaining a one-time frozen legacy bootstrap and requiring a careful bridge for unusual old volumes. The [Alembic cookbook](https://alembic.sqlalchemy.org/en/latest/cookbook.html) documents the bootstrap/stamp pattern; this project deliberately executes idempotent baseline DDL instead of blindly stamping existing databases.
