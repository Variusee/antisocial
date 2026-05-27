from alembic import op


revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS banner_state (
            id INT PRIMARY KEY DEFAULT 1,
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            circle_x INT NOT NULL DEFAULT 0,
            circle_y INT NOT NULL DEFAULT 0,
            circle_r INT NOT NULL DEFAULT 0,
            font_size INT NOT NULL DEFAULT 0,
            last_count INT NOT NULL DEFAULT -1,
            last_updated_at BIGINT NOT NULL DEFAULT 0,
            last_error TEXT,
            CHECK (id = 1)
        )
    """)
    op.execute("INSERT INTO banner_state (id) VALUES (1) ON CONFLICT DO NOTHING")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS banner_state")
