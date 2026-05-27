from alembic import op


revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS voice_hours (
            id SERIAL PRIMARY KEY,
            guild_id BIGINT NOT NULL,
            user_id BIGINT NOT NULL,
            day_key TEXT NOT NULL,
            seconds BIGINT NOT NULL DEFAULT 0,
            updated_at BIGINT NOT NULL DEFAULT 0,
            UNIQUE(guild_id, user_id, day_key)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_vh_guild_day ON voice_hours(guild_id, day_key)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_vh_user ON voice_hours(user_id)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS private_voice (
            user_id BIGINT PRIMARY KEY,
            name TEXT,
            user_limit INTEGER,
            is_closed BOOLEAN DEFAULT FALSE
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS private_voice_bans (
            owner_id BIGINT NOT NULL,
            banned_user_id BIGINT NOT NULL,
            UNIQUE(owner_id, banned_user_id)
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS private_voice_mutes (
            owner_id BIGINT NOT NULL,
            muted_user_id BIGINT NOT NULL,
            UNIQUE(owner_id, muted_user_id)
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS active_voice_channels (
            channel_id BIGINT PRIMARY KEY,
            owner_id BIGINT NOT NULL
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS active_voice_channels")
    op.execute("DROP TABLE IF EXISTS private_voice_mutes")
    op.execute("DROP TABLE IF EXISTS private_voice_bans")
    op.execute("DROP TABLE IF EXISTS private_voice")
    op.execute("DROP INDEX IF EXISTS idx_vh_user")
    op.execute("DROP INDEX IF EXISTS idx_vh_guild_day")
    op.execute("DROP TABLE IF EXISTS voice_hours")
