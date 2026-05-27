from alembic import op


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE stacks DROP COLUMN IF EXISTS hours")

    op.execute("""
        CREATE TABLE IF NOT EXISTS stack_reviewers (
            id SERIAL PRIMARY KEY,
            stack_id INTEGER NOT NULL REFERENCES stacks(stack_id) ON DELETE CASCADE,
            user_id BIGINT NOT NULL,
            added_at BIGINT NOT NULL,
            added_by BIGINT NOT NULL,
            UNIQUE (stack_id, user_id)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_reviewers_stack ON stack_reviewers(stack_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_reviewers_user ON stack_reviewers(user_id)")

    op.execute("""
        CREATE TABLE IF NOT EXISTS stack_application_messages (
            id SERIAL PRIMARY KEY,
            application_id INTEGER NOT NULL REFERENCES stack_applications(id) ON DELETE CASCADE,
            recipient_id BIGINT NOT NULL,
            channel_id BIGINT NOT NULL,
            message_id BIGINT NOT NULL,
            UNIQUE (application_id, recipient_id)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_app_msgs_app ON stack_application_messages(application_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_app_msgs_msgid ON stack_application_messages(message_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS stack_application_messages")
    op.execute("DROP TABLE IF EXISTS stack_reviewers")
    op.execute("ALTER TABLE stacks ADD COLUMN IF NOT EXISTS hours INTEGER DEFAULT 0")
