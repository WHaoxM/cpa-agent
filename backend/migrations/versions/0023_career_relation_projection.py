"""Store evidence-backed career relation snapshots after account preferences."""

from alembic import op
import re
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


revision = "0023_career_relation_projection"
down_revision = "0022_account_profile_preferences"
branch_labels = None
depends_on = None


def _tables():
    metadata = sa.MetaData()
    # The revision table is owned by migration 0020. This declaration supplies
    # the referenced identity to SQLAlchemy; it is never created here.
    sa.Table(
        "career_pathway_revision",
        metadata,
        sa.Column("revision", sa.String(64), primary_key=True),
        sa.Column("role_id", sa.String(64), nullable=False),
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )

    runs = sa.Table(
        "career_relation_run",
        metadata,
        sa.Column("run_id", mysql.CHAR(64, charset="ascii", collation="ascii_bin"), primary_key=True),
        sa.Column("algorithm_version", mysql.VARCHAR(64, charset="ascii", collation="ascii_bin"), nullable=False),
        sa.Column("input_sha256", mysql.CHAR(64, charset="ascii", collation="ascii_bin"), nullable=False),
        sa.Column("source_manifest_json", sa.JSON(), nullable=False),
        sa.Column("source_count", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("node_count", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("edge_count", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP(6)")),
        sa.UniqueConstraint(
            "algorithm_version", "input_sha256",
            name="uq_career_relation_run_algorithm_input",
        ),
        sa.CheckConstraint("source_count >= 0", name="ck_career_relation_run_source_nonnegative"),
        sa.CheckConstraint("node_count >= 0", name="ck_career_relation_run_node_nonnegative"),
        sa.CheckConstraint("edge_count >= 0", name="ck_career_relation_run_edge_nonnegative"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )

    edges = sa.Table(
        "career_role_relation",
        metadata,
        sa.Column("edge_id", mysql.CHAR(64, charset="ascii", collation="ascii_bin"), primary_key=True),
        sa.Column("run_id", mysql.CHAR(64, charset="ascii", collation="ascii_bin"), nullable=False),
        sa.Column("source_role_id", sa.String(64), nullable=False),
        sa.Column("source_revision", sa.String(64), nullable=False),
        sa.Column("source_sha256", mysql.CHAR(64, charset="ascii", collation="ascii_bin"), nullable=False),
        sa.Column("target_role_id", sa.String(64), nullable=False),
        sa.Column("target_revision", sa.String(64), nullable=False),
        sa.Column("target_sha256", mysql.CHAR(64, charset="ascii", collation="ascii_bin"), nullable=False),
        sa.Column("relation_type", mysql.VARCHAR(32, charset="ascii", collation="ascii_bin"), nullable=False),
        sa.Column("weight", sa.Numeric(10, 8), nullable=False),
        sa.Column("shared_skill_count", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("union_skill_count", mysql.INTEGER(unsigned=True), nullable=False),
        sa.Column("shared_skill_ids_json", sa.JSON(), nullable=False),
        sa.Column("evidence_json", sa.JSON(), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP(6)")),
        sa.ForeignKeyConstraint(
            ["run_id"], ["career_relation_run.run_id"],
            name="fk_career_role_relation_run",
        ),
        sa.ForeignKeyConstraint(
            ["source_revision"], ["career_pathway_revision.revision"],
            name="fk_career_role_relation_source_revision",
        ),
        sa.ForeignKeyConstraint(
            ["target_revision"], ["career_pathway_revision.revision"],
            name="fk_career_role_relation_target_revision",
        ),
        sa.UniqueConstraint(
            "run_id", "source_role_id", "target_role_id", "relation_type",
            name="uq_career_role_relation_run_pair_type",
        ),
        sa.CheckConstraint("weight >= 0 AND weight <= 1", name="ck_career_role_relation_weight_range"),
        sa.CheckConstraint("shared_skill_count > 0", name="ck_career_role_relation_shared_positive"),
        sa.CheckConstraint("union_skill_count >= shared_skill_count", name="ck_career_role_relation_union_ge_shared"),
        sa.CheckConstraint("source_role_id <> target_role_id", name="ck_career_role_relation_distinct_roles"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    sa.Index("ix_career_role_relation_source", edges.c.source_role_id)
    sa.Index("ix_career_role_relation_target", edges.c.target_role_id)
    sa.Index("ix_career_role_relation_run", edges.c.run_id)
    return runs, edges


def _assert_table_matches(bind, expected):
    inspector = sa.inspect(bind)
    actual = sa.Table(expected.name, sa.MetaData(), autoload_with=bind)
    expected_names = set(expected.c.keys())
    if set(actual.c.keys()) != expected_names:
        raise RuntimeError(f"Partial migration column drift: {expected.name}")
    for column in expected.c:
        found = actual.c[column.name]
        if found.nullable != column.nullable or op.get_context().impl.compare_type(found, column):
            raise RuntimeError(f"Partial migration type drift: {expected.name}.{column.name}")
    if {column.name for column in actual.primary_key} != {column.name for column in expected.primary_key}:
        raise RuntimeError(f"Partial migration primary-key drift: {expected.name}")

    expected_foreign_keys = {
        (item.name, tuple(item.column_keys), tuple(element.target_fullname for element in item.elements))
        for item in expected.foreign_key_constraints
    }
    actual_foreign_keys = {
        (item.get("name"), tuple(item.get("constrained_columns") or ()),
         tuple(f"{item.get('referred_table')}.{name}" for name in item.get("referred_columns") or ()))
        for item in inspector.get_foreign_keys(expected.name)
    }
    if actual_foreign_keys != expected_foreign_keys:
        raise RuntimeError(f"Partial migration foreign-key drift: {expected.name}")

    expected_unique = {
        (item.name, tuple(column.name for column in item.columns))
        for item in expected.constraints if isinstance(item, sa.UniqueConstraint)
    }
    actual_unique = {
        (item.get("name"), tuple(item.get("column_names") or ()))
        for item in inspector.get_unique_constraints(expected.name)
    }
    if actual_unique != expected_unique:
        raise RuntimeError(f"Partial migration unique-key drift: {expected.name}")

    expected_indexes = {
        item.name: (tuple(column.name for column in item.columns), bool(item.unique))
        for item in expected.indexes
    }
    actual_indexes = {
        item.get("name"): (tuple(item.get("column_names") or ()), bool(item.get("unique")))
        for item in inspector.get_indexes(expected.name)
        if item.get("name") in expected_indexes
    }
    if actual_indexes != expected_indexes:
        raise RuntimeError(f"Partial migration index drift: {expected.name}")

    normalize_check = lambda value: re.sub(r"[\s`()]+", "", str(value)).lower()
    expected_checks = {
        item.name: normalize_check(item.sqltext)
        for item in expected.constraints if isinstance(item, sa.CheckConstraint)
    }
    actual_checks = {
        item.get("name"): normalize_check(item.get("sqltext", ""))
        for item in inspector.get_check_constraints(expected.name)
    }
    if actual_checks != expected_checks:
        raise RuntimeError(f"Partial migration check-constraint drift: {expected.name}")


def _ensure_table(bind, table):
    if not sa.inspect(bind).has_table(table.name):
        table.create(bind, checkfirst=False)
    _assert_table_matches(bind, table)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("career_pathway_revision"):
        raise RuntimeError("career_pathway_revision is required before relation projection")
    parent = sa.Table("career_pathway_revision", sa.MetaData(), autoload_with=bind)
    for name in ("revision", "role_id"):
        if name not in parent.c or op.get_context().impl.compare_type(
            parent.c[name], sa.Column(name, sa.String(64), nullable=False)
        ):
            raise RuntimeError(f"career_pathway_revision has incompatible {name} identity")

    runs, edges = _tables()
    _ensure_table(bind, runs)
    _ensure_table(bind, edges)


def downgrade() -> None:
    raise RuntimeError("Persisted career relation snapshots are not automatically downgraded")
