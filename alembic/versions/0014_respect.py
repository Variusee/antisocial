from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS reputation (
            id SERIAL PRIMARY KEY,
            from_user_id BIGINT NOT NULL,
            to_user_id BIGINT NOT NULL,
            amount INTEGER NOT NULL DEFAULT 1,
            reason TEXT DEFAULT '',
            created_at BIGINT NOT NULL,
            UNIQUE(from_user_id, to_user_id)
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_reputation_to ON reputation(to_user_id)
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_reputation_from ON reputation(from_user_id)
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS reputation_log (
            id SERIAL PRIMARY KEY,
            from_user_id BIGINT NOT NULL,
            to_user_id BIGINT NOT NULL,
            amount INTEGER NOT NULL,
            old_reputation INTEGER NOT NULL,
            new_reputation INTEGER NOT NULL,
            reason TEXT DEFAULT '',
            created_at BIGINT NOT NULL
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_rep_log_to ON reputation_log(to_user_id)
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_rep_log_created ON reputation_log(created_at DESC)
    """)

    op.execute("""
        CREATE OR REPLACE FUNCTION update_reputation_trigger()
        RETURNS TRIGGER AS $$
        BEGIN
            INSERT INTO reputation_log (from_user_id, to_user_id, amount, old_reputation, new_reputation, reason, created_at)
            VALUES (NEW.from_user_id, NEW.to_user_id, NEW.amount, OLD.reputation, NEW.reputation, NEW.reason, NEW.created_at);
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)

    op.execute("""
        DROP TRIGGER IF EXISTS reputation_update_trigger ON reputation
    """)

    op.execute("""
        CREATE TRIGGER reputation_update_trigger
            AFTER UPDATE ON reputation
            FOR EACH ROW
            EXECUTE FUNCTION update_reputation_trigger()
    """)

    op.execute("""
        ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS reputation INTEGER DEFAULT 0
    """)

    op.execute("""
        ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS reputation_received INTEGER DEFAULT 0
    """)

    op.execute("""
        ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS reputation_given INTEGER DEFAULT 0
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS reputation_update_trigger ON reputation")
    op.execute("DROP FUNCTION IF EXISTS update_reputation_trigger")
    op.execute("DROP TABLE IF EXISTS reputation_log")
    op.execute("DROP TABLE IF EXISTS reputation")
    op.execute("ALTER TABLE user_profiles DROP COLUMN IF EXISTS reputation")
    op.execute("ALTER TABLE user_profiles DROP COLUMN IF EXISTS reputation_received")
    op.execute("ALTER TABLE user_profiles DROP COLUMN IF EXISTS reputation_given")