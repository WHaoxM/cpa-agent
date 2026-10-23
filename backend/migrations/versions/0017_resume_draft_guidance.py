"""Keep server-bound diagnostic context beside the single existing draft."""
from alembic import op
import sqlalchemy as sa

revision='0017_resume_draft_guidance'
down_revision='0016_career_exploration'
branch_labels=None
depends_on=None


def upgrade():
    op.add_column('student_resume_draft',sa.Column('guidance_json',sa.JSON(),nullable=True))


def downgrade():
    raise RuntimeError('Do not discard resume guidance automatically')
