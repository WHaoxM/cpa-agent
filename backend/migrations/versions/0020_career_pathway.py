"""Versioned public role facts, salary references and skill/course relations.

Additive only: existing accounts, publications and personal results are untouched.
"""
from alembic import op
import sqlalchemy as sa

revision = '0020_career_pathway'
down_revision = '0019_conversation_task_context'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    # Existing deployments have different course collations. A foreign key must
    # use the actual referenced column's collation, not rewrite the old table.
    course_identity = bind.execute(sa.text("SELECT CHARACTER_SET_NAME,COLLATION_NAME "
        "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() "
        "AND TABLE_NAME='course' AND COLUMN_NAME='course_id'")).mappings().one()
    if course_identity['CHARACTER_SET_NAME'] != 'utf8mb4':
        raise RuntimeError('Unsupported course identity charset; inspect before migrating')

    def create(name, *columns, **kwargs):
        if not sa.inspect(bind).has_table(name):
            op.create_table(name, *columns, **kwargs)
            return
        # MySQL DDL auto-commits. Safely resume a partial attempt only when the
        # previously created objects match this revision's columns/types/keys.
        # This bounded retry check is not a full schema-drift reconciler.
        existing = sa.Table(name, sa.MetaData(), autoload_with=bind)
        expected = sa.Table(name, sa.MetaData(), *columns, **kwargs)
        if set(existing.c.keys()) != set(expected.c.keys()):
            raise RuntimeError('Partial migration column drift: ' + name)
        for c in expected.c:
            previous = existing.c[c.name]
            if previous.nullable != c.nullable or op.get_context().impl.compare_type(previous, c):
                raise RuntimeError('Partial migration type drift: ' + name + '.' + c.name)
        if {c.name for c in existing.primary_key} != {c.name for c in expected.primary_key}:
            raise RuntimeError('Partial migration key drift: ' + name)
        old_fk = {(tuple(f.column_keys), tuple(e.target_fullname for e in f.elements))
                  for f in existing.foreign_key_constraints}
        new_fk = {(tuple(f.column_keys), tuple(e.target_fullname for e in f.elements))
                  for f in expected.foreign_key_constraints}
        if old_fk != new_fk:
            raise RuntimeError('Partial migration foreign key drift: ' + name)

    options = dict(mysql_engine='InnoDB', mysql_charset='utf8mb4', mysql_collate='utf8mb4_unicode_ci')
    create('career_pathway_revision',
        sa.Column('revision', sa.String(64), primary_key=True),
        sa.Column('role_id', sa.String(64), nullable=False),
        sa.Column('role_name', sa.String(100), nullable=False),
        sa.Column('schema_version', sa.String(40), nullable=False),
        sa.Column('content_sha256', sa.String(64), nullable=False),
        sa.Column('snapshot_json', sa.JSON(), nullable=False),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('curation_method', sa.String(64), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Index('ix_pathway_role_status', 'role_id', 'status'), **options)

    def rev():
        return sa.Column('revision', sa.String(64), sa.ForeignKey('career_pathway_revision.revision'), primary_key=True)

    create('career_job_fact', rev(),
        sa.Column('fact_id', sa.String(80), primary_key=True),
        sa.Column('employer', sa.String(200), nullable=False),
        sa.Column('title', sa.String(200), nullable=False),
        sa.Column('locations_json', sa.JSON(), nullable=False),
        sa.Column('requirements_json', sa.JSON(), nullable=False),
        sa.Column('source_uri', sa.String(1000), nullable=False),
        sa.Column('source_sha256', sa.String(64), nullable=False),
        sa.Column('source_fetched_at', sa.String(40), nullable=False),
        sa.Column('vacancy_validity', sa.String(16), nullable=False),
        sa.Column('salary_quote_json', sa.JSON(), nullable=True),
        sa.Column('usage', sa.String(24), nullable=False),
        sa.Column('fact_json', sa.JSON(), nullable=False), **options)
    create('career_salary_reference', rev(),
        sa.Column('reference_id', sa.String(80), primary_key=True),
        sa.Column('kind', sa.String(24), nullable=False),
        sa.Column('city', sa.String(80), nullable=False),
        sa.Column('experience_level', sa.String(80), nullable=False),
        sa.Column('currency', sa.String(8), nullable=False),
        sa.Column('period', sa.String(16), nullable=False),
        sa.Column('lower_amount', sa.Numeric(12, 2), nullable=False),
        sa.Column('upper_amount', sa.Numeric(12, 2), nullable=False),
        sa.Column('as_of', sa.String(40), nullable=False),
        sa.Column('method', sa.Text(), nullable=False),
        sa.Column('basis_json', sa.JSON(), nullable=False),
        sa.Column('uncertainty', sa.Text(), nullable=False),
        sa.Column('reference_json', sa.JSON(), nullable=False),
        sa.CheckConstraint('lower_amount >= 0 AND upper_amount >= lower_amount', name='ck_salary_reference_range'), **options)
    create('career_role_skill', rev(),
        sa.Column('skill_id', sa.BigInteger(), sa.ForeignKey('skill_taxonomy.id'), primary_key=True),
        sa.Column('tier', sa.String(24), nullable=False),
        sa.Column('requirement_kind', sa.String(24), nullable=False),
        sa.Column('evidence_json', sa.JSON(), nullable=False), **options)
    create('career_skill_course', rev(),
        sa.Column('skill_id', sa.BigInteger(), sa.ForeignKey('skill_taxonomy.id'), primary_key=True),
        sa.Column('course_id', sa.String(64, collation=course_identity['COLLATION_NAME']), sa.ForeignKey('course.course_id'), primary_key=True),
        sa.Column('evidence_json', sa.JSON(), nullable=False), **options)
    create('career_skill_prerequisite', rev(),
        sa.Column('from_skill_id', sa.BigInteger(), sa.ForeignKey('skill_taxonomy.id'), primary_key=True),
        sa.Column('to_skill_id', sa.BigInteger(), sa.ForeignKey('skill_taxonomy.id'), primary_key=True),
        sa.Column('evidence_json', sa.JSON(), nullable=False),
        sa.CheckConstraint('from_skill_id <> to_skill_id', name='ck_pathway_no_self_prerequisite'), **options)
    create('career_pathway_projection', rev(),
        sa.Column('target', sa.String(16), primary_key=True),
        sa.Column('status', sa.String(16), nullable=False),
        sa.Column('content_sha256', sa.String(64), nullable=False),
        sa.Column('details_json', sa.JSON(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()), **options)


def downgrade():
    raise RuntimeError('Public evidence versions must be retained; withdraw a revision instead')
