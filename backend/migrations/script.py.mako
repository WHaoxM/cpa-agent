"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision}
"""

from alembic import op
import sqlalchemy as sa


revision = ${repr(up_revision)}
down_revision = ${repr(down_revision)}
branch_labels = ${repr(branch_labels)}
depends_on = ${repr(depends_on)}


def upgrade() -> None:
    raise NotImplementedError("Write and review an explicit MySQL migration")


def downgrade() -> None:
    raise RuntimeError("No automatic data-destructive downgrade")
