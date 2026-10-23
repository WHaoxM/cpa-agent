"""Keep editable resume drafts separate from confirmed student evidence."""
from alembic import op
import sqlalchemy as sa

revision = "0014_student_resume_draft"
down_revision = "0013_saved_job_pub_binding"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("student_resume_draft",
        sa.Column("student_id", sa.String(64), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("form_json", sa.Text(), nullable=False),
        sa.Column("theme", sa.String(7), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()))


def downgrade():
    raise RuntimeError("Do not discard student drafts automatically")
