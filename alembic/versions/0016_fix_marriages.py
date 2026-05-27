from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS marriages (
            id SERIAL PRIMARY KEY,
            user1_id BIGINT NOT NULL,
            user2_id BIGINT NOT NULL,
            married_at BIGINT NOT NULL,
            divorced_at BIGINT DEFAULT 0,
            proposer_id BIGINT NOT NULL,
            love_points INTEGER DEFAULT 0,
            balance BIGINT DEFAULT 0,
            plata_until BIGINT DEFAULT 0,
            is_active BOOLEAN DEFAULT TRUE,
            UNIQUE(user1_id, user2_id)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_marriages_user1 ON marriages(user1_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_marriages_user2 ON marriages(user2_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_marriages_active ON marriages(is_active)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS marriages")