"""update_run: one row per attempt of the daily update

The dashboard shows the last one. grafana_reader can read it through the default privileges of 0003.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-05 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0004'
down_revision: Union[str, Sequence[str], None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('update_run',
    sa.Column('id', sa.BigInteger(), nullable=False),
    sa.Column('attempt', sa.Integer(), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('scanned', sa.Integer(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.CheckConstraint("status IN ('ok', 'failed')", name=op.f('update_run_status_check')),
    sa.PrimaryKeyConstraint('id', name=op.f('update_run_pkey'))
    )


def downgrade() -> None:
    op.drop_table('update_run')
