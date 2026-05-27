from alembic import op


revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS stack_tag_warnings (
            user_id BIGINT PRIMARY KEY,
            stack_id INTEGER NOT NULL,
            warned_at BIGINT NOT NULL DEFAULT 0,
            deadline_at BIGINT NOT NULL DEFAULT 0,
            reminded_dm BOOLEAN NOT NULL DEFAULT FALSE
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_stw_deadline ON stack_tag_warnings(deadline_at)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_stw_stack ON stack_tag_warnings(stack_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_stw_deadline")
    op.execute("DROP INDEX IF EXISTS idx_stw_stack")
    op.execute("DROP TABLE IF EXISTS stack_tag_warnings")
