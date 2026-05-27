from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE stacks ADD COLUMN IF NOT EXISTS balance BIGINT DEFAULT 0")
    op.execute("ALTER TABLE stacks ADD COLUMN IF NOT EXISTS level INTEGER DEFAULT 1")
    op.execute("ALTER TABLE stacks ADD COLUMN IF NOT EXISTS exp BIGINT DEFAULT 0")
    op.execute("ALTER TABLE stacks ADD COLUMN IF NOT EXISTS icon_url TEXT DEFAULT ''")


def downgrade() -> None:
    op.execute("ALTER TABLE stacks DROP COLUMN IF EXISTS balance")
    op.execute("ALTER TABLE stacks DROP COLUMN IF EXISTS level")
    op.execute("ALTER TABLE stacks DROP COLUMN IF EXISTS exp")
    op.execute("ALTER TABLE stacks DROP COLUMN IF EXISTS icon_url")