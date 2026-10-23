"""Versioned task context on the existing conversation, not a second workflow store."""
from alembic import op
import sqlalchemy as sa

revision = '0019_conversation_task_context'
down_revision = '0018_exploration_analysis'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('conversation', sa.Column('task_context_json', sa.JSON(), nullable=True))
    op.add_column('conversation', sa.Column('task_context_revision', sa.Integer(), nullable=False, server_default='0'))


def downgrade():
    raise RuntimeError('Task handoffs and continuation history must be preserved')
