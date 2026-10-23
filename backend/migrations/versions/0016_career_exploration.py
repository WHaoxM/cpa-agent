"""Persist owner explorations and immutable trial rounds beside existing plans."""
from alembic import op
import sqlalchemy as sa

revision = '0016_career_exploration'
down_revision = '0015_self_reflection'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('career_exploration',
        sa.Column('exploration_id', sa.String(64), primary_key=True),
        sa.Column('student_id', sa.String(64), nullable=False),
        sa.Column('request_id', sa.String(64), nullable=False),
        sa.Column('session_id', sa.String(64), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('draft_json', sa.JSON(), nullable=False),
        sa.Column('sources_json', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('student_id', 'request_id', name='uq_exploration_request'),
        sa.Index('idx_exploration_owner', 'student_id', 'created_at'),
        mysql_engine='InnoDB', mysql_charset='utf8mb4')
    op.create_table('career_exploration_round',
        sa.Column('round_id', sa.String(64), primary_key=True),
        sa.Column('exploration_id', sa.String(64), sa.ForeignKey('career_exploration.exploration_id'), nullable=False),
        sa.Column('ordinal', sa.Integer(), nullable=False),
        sa.Column('selection_json', sa.JSON(), nullable=False),
        sa.Column('sources_json', sa.JSON(), nullable=False),
        sa.Column('plan_id', sa.String(64), nullable=True),
        sa.Column('review_json', sa.JSON(), nullable=True),
        sa.Column('decision_json', sa.JSON(), nullable=True),
        sa.Column('closed_reason', sa.String(40), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('exploration_id', 'ordinal', name='uq_exploration_round'),
        mysql_engine='InnoDB', mysql_charset='utf8mb4')
    op.add_column('agent_action_plan', sa.Column('exploration_round_id', sa.String(64), nullable=True))
    op.create_unique_constraint('uq_plan_exploration_round', 'agent_action_plan', ['exploration_round_id'])


def downgrade():
    raise RuntimeError('Exploration choices and practice history must not be discarded automatically')
