from alembic import op


revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS giveaway_voice_targets (
            message_id TEXT NOT NULL,
            target_id BIGINT NOT NULL,
            PRIMARY KEY (message_id, target_id)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_gwvt_message ON giveaway_voice_targets(message_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_gwvt_message")
    op.execute("DROP TABLE IF EXISTS giveaway_voice_targets")
