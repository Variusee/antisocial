from alembic import op


revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE stacks ADD COLUMN IF NOT EXISTS recruitment_open BOOLEAN DEFAULT TRUE")


def downgrade() -> None:
    op.execute("ALTER TABLE stacks DROP COLUMN IF EXISTS recruitment_open")
