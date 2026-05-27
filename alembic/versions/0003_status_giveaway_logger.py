from alembic import op


revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS bot_status_msg (
            bot_name TEXT PRIMARY KEY,
            channel_id BIGINT NOT NULL,
            message_id BIGINT NOT NULL
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS giveaways (
            message_id TEXT PRIMARY KEY,
            prize TEXT NOT NULL,
            winners INTEGER NOT NULL,
            end_time DOUBLE PRECISION NOT NULL,
            start_time DOUBLE PRECISION DEFAULT 0,
            type TEXT NOT NULL,
            target_id BIGINT DEFAULT 0,
            channel_id BIGINT NOT NULL,
            guild_id BIGINT NOT NULL,
            ended BOOLEAN DEFAULT FALSE,
            winners_list TEXT DEFAULT '[]'
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_gw_ended_end ON giveaways(ended, end_time)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_gw_guild ON giveaways(guild_id)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS gw_participants (
            message_id TEXT NOT NULL,
            user_id BIGINT NOT NULL,
            UNIQUE(message_id, user_id)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_gw_p_msg ON gw_participants(message_id)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS gw_invites (
            message_id TEXT NOT NULL,
            inviter_id BIGINT NOT NULL,
            code TEXT NOT NULL,
            UNIQUE(message_id, inviter_id),
            UNIQUE(message_id, code)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_gw_inv_code ON gw_invites(code)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS gw_invite_joins (
            id SERIAL PRIMARY KEY,
            message_id TEXT NOT NULL,
            inviter_id BIGINT NOT NULL,
            joined_user_id BIGINT NOT NULL,
            joined_at BIGINT NOT NULL,
            status TEXT DEFAULT 'pending',
            rejection_reason TEXT,
            manually_overridden BOOLEAN DEFAULT FALSE,
            overridden_by BIGINT DEFAULT 0,
            overridden_at BIGINT DEFAULT 0,
            suspicion_score INTEGER DEFAULT 0,
            UNIQUE(message_id, joined_user_id)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_gwij_msg_inv ON gw_invite_joins(message_id, inviter_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_gwij_status ON gw_invite_joins(message_id, status)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_gwij_joined_at ON gw_invite_joins(joined_at)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_gwij_joined_user ON gw_invite_joins(joined_user_id)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS historical_joins (
            guild_id BIGINT NOT NULL,
            user_id BIGINT NOT NULL,
            first_joined_at BIGINT DEFAULT 0,
            UNIQUE(guild_id, user_id)
        )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS historical_joins")
    op.execute("DROP TABLE IF EXISTS gw_invite_joins")
    op.execute("DROP TABLE IF EXISTS gw_invites")
    op.execute("DROP TABLE IF EXISTS gw_participants")
    op.execute("DROP TABLE IF EXISTS giveaways")
    op.execute("DROP TABLE IF EXISTS bot_status_msg")
