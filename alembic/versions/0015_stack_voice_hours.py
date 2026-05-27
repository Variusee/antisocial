from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS stack_voice_hours (
            id SERIAL PRIMARY KEY,
            stack_id INTEGER NOT NULL REFERENCES stacks(stack_id) ON DELETE CASCADE,
            user_id BIGINT NOT NULL,
            seconds BIGINT NOT NULL DEFAULT 0,
            last_update BIGINT NOT NULL DEFAULT 0,
            UNIQUE(stack_id, user_id)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_svh_stack ON stack_voice_hours(stack_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_svh_user ON stack_voice_hours(user_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS stack_voice_hours")