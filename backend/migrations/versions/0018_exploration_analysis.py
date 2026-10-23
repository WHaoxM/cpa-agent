"""Bind read-only companion turns to existing durable Agent runs."""
from alembic import op
import sqlalchemy as sa

revision = '0018_exploration_analysis'
down_revision = '0017_resume_draft_guidance'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('career_exploration_analysis_turn',
        sa.Column('turn_id', sa.String(64), primary_key=True),
        sa.Column('exploration_id', sa.String(64), sa.ForeignKey('career_exploration.exploration_id'), nullable=False),
        sa.Column('run_id', sa.String(64, collation='utf8mb4_bin'), sa.ForeignKey('agent_run.run_id'), nullable=False, unique=True),
        sa.Column('request_key', sa.String(128, collation='utf8mb4_bin'), nullable=False),
        sa.Column('request_hash', sa.String(64), nullable=False),
        sa.Column('ordinal', sa.Integer(), nullable=False),
        sa.Column('round_id', sa.String(64), nullable=True),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('context_json', sa.JSON(), nullable=False),
        sa.Column('result_json', sa.JSON(), nullable=True),
        sa.Column('confirmed_round_id', sa.String(64), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint('exploration_id', 'request_key', name='uq_exploration_analysis_key'),
        sa.UniqueConstraint('exploration_id', 'ordinal', name='uq_exploration_analysis_ordinal'),
        mysql_engine='InnoDB', mysql_charset='utf8mb4')


def downgrade():
    raise RuntimeError('Exploration discussion history must not be discarded automatically')
