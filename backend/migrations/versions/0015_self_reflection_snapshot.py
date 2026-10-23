"""Preserve owner-bound self-reflection questions and answers, without scores."""
from alembic import op
import sqlalchemy as sa

revision = '0015_self_reflection'
down_revision = '0014_student_resume_draft'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('student_self_assessment', sa.Column('reflection_snapshot', sa.JSON(), nullable=True))
    op.add_column('student_self_assessment', sa.Column('submission_id', sa.String(64, collation='utf8mb4_bin'), nullable=True))
    op.create_unique_constraint('uq_reflection_owner_submission', 'student_self_assessment', ['student_id', 'submission_id'])


def downgrade():
    raise RuntimeError('Do not discard saved self-reflection snapshots automatically')
