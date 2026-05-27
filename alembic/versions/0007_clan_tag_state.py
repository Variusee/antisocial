from alembic import op


revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS clan_tag_state (
            user_id BIGINT PRIMARY KEY,
            is_wearing BOOLEAN NOT NULL DEFAULT FALSE,
            last_changed_at BIGINT NOT NULL DEFAULT 0,
            first_detected_at BIGINT NOT NULL DEFAULT 0
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_cts_wearing ON clan_tag_state(is_wearing) WHERE is_wearing = TRUE")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_cts_wearing")
    op.execute("DROP TABLE IF EXISTS clan_tag_state")
