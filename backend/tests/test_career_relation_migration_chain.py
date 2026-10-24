from __future__ import annotations

import importlib.util
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def test_integrated_career_relation_migration_is_one_linear_head_after_account_profile():
    config = Config()
    config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    scripts = ScriptDirectory.from_config(config)

    assert scripts.get_heads() == ["0023_career_relation_projection"]
    migration = scripts.get_revision("0023_career_relation_projection")
    assert migration.down_revision == "0022_account_profile_preferences"


def test_relation_migration_schema_has_source_run_and_revision_constraints():
    migration_path = BACKEND_ROOT / "migrations" / "versions" / "0023_career_relation_projection.py"
    spec = importlib.util.spec_from_file_location("career_relation_migration", migration_path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    runs, edges = migration._tables()

    assert runs.name == "career_relation_run"
    assert {item.name for item in runs.constraints if item.name} >= {
        "uq_career_relation_run_algorithm_input",
        "ck_career_relation_run_source_nonnegative",
        "ck_career_relation_run_node_nonnegative",
        "ck_career_relation_run_edge_nonnegative",
    }
    assert edges.name == "career_role_relation"
    assert {item.name for item in edges.foreign_key_constraints} == {
        "fk_career_role_relation_run",
        "fk_career_role_relation_source_revision",
        "fk_career_role_relation_target_revision",
    }
    assert {item.name for item in edges.constraints if item.name} >= {
        "uq_career_role_relation_run_pair_type",
        "ck_career_role_relation_weight_range",
        "ck_career_role_relation_shared_positive",
        "ck_career_role_relation_union_ge_shared",
        "ck_career_role_relation_distinct_roles",
    }
    assert {item.name for item in edges.indexes} == {
        "ix_career_role_relation_source",
        "ix_career_role_relation_target",
        "ix_career_role_relation_run",
    }
