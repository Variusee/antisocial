from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS economy (
            user_id BIGINT PRIMARY KEY,
            coins BIGINT DEFAULT 0,
            total_coins_earned BIGINT DEFAULT 0,
            total_coins_spent BIGINT DEFAULT 0,
            anticoin BIGINT DEFAULT 0,
            total_anticoin_earned BIGINT DEFAULT 0,
            total_anticoin_spent BIGINT DEFAULT 0,
            bank_coins BIGINT DEFAULT 0,
            bank_anticoin BIGINT DEFAULT 0,
            last_daily BIGINT DEFAULT 0,
            daily_streak INTEGER DEFAULT 0,
            last_weekly BIGINT DEFAULT 0,
            weekly_streak INTEGER DEFAULT 0,
            last_monthly BIGINT DEFAULT 0,
            monthly_streak INTEGER DEFAULT 0,
            created_at BIGINT DEFAULT 0,
            updated_at BIGINT DEFAULT 0
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS shop (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            coins_price BIGINT DEFAULT 0,
            anticoin_price BIGINT DEFAULT 0,
            role_id BIGINT DEFAULT 0,
            role_name TEXT DEFAULT '',
            item_type TEXT DEFAULT 'role',
            is_limited BOOLEAN DEFAULT FALSE,
            available_until BIGINT DEFAULT 0,
            created_at BIGINT NOT NULL
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS inventory (
            user_id BIGINT NOT NULL,
            item_id INTEGER REFERENCES shop(id) ON DELETE CASCADE,
            quantity INTEGER DEFAULT 1,
            obtained_at BIGINT NOT NULL,
            PRIMARY KEY (user_id, item_id)
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS daily_rewards_config (
            day INTEGER PRIMARY KEY,
            coins_reward BIGINT NOT NULL,
            anticoin_reward BIGINT DEFAULT 0
        )
    """)

    op.execute("""
        INSERT INTO daily_rewards_config (day, coins_reward, anticoin_reward) VALUES
            (1, 100, 0), (2, 110, 0), (3, 120, 0), (4, 130, 0), (5, 140, 0),
            (6, 150, 0), (7, 200, 5), (8, 160, 0), (9, 170, 0), (10, 180, 0),
            (11, 190, 0), (12, 200, 0), (13, 210, 0), (14, 250, 10), (15, 220, 0),
            (16, 230, 0), (17, 240, 0), (18, 250, 0), (19, 260, 0), (20, 270, 0),
            (21, 300, 15), (22, 280, 0), (23, 290, 0), (24, 300, 0), (25, 310, 0),
            (26, 320, 0), (27, 330, 0), (28, 350, 20), (29, 340, 0), (30, 500, 50)
        ON CONFLICT (day) DO NOTHING
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS weekly_rewards_config (
            week INTEGER PRIMARY KEY,
            coins_reward BIGINT NOT NULL,
            anticoin_reward BIGINT DEFAULT 0
        )
    """)

    op.execute("""
        INSERT INTO weekly_rewards_config (week, coins_reward, anticoin_reward) VALUES
            (1, 500, 5), (2, 600, 10), (3, 700, 15), (4, 1000, 25)
        ON CONFLICT (week) DO NOTHING
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS monthly_rewards_config (
            month INTEGER PRIMARY KEY,
            coins_reward BIGINT NOT NULL,
            anticoin_reward BIGINT DEFAULT 0
        )
    """)

    op.execute("""
        INSERT INTO monthly_rewards_config (month, coins_reward, anticoin_reward) VALUES
            (1, 2000, 20), (2, 2500, 30), (3, 3000, 40), (6, 5000, 75), (12, 10000, 150)
        ON CONFLICT (month) DO NOTHING
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS boosters (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            type TEXT NOT NULL,
            multiplier INTEGER DEFAULT 1,
            expires_at BIGINT NOT NULL,
            created_at BIGINT NOT NULL
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS lottery (
            id SERIAL PRIMARY KEY,
            ticket_id TEXT UNIQUE NOT NULL,
            user_id BIGINT NOT NULL,
            coins_cost BIGINT NOT NULL,
            created_at BIGINT NOT NULL,
            is_winner BOOLEAN DEFAULT FALSE,
            prize_coins BIGINT DEFAULT 0,
            prize_anticoin BIGINT DEFAULT 0
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS lottery_config (
            id INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
            ticket_price BIGINT DEFAULT 100,
            jackpot_coins BIGINT DEFAULT 10000,
            jackpot_anticoin BIGINT DEFAULT 100,
            last_draw_at BIGINT DEFAULT 0,
            next_draw_at BIGINT DEFAULT 0,
            winners_count INTEGER DEFAULT 3
        )
    """)

    op.execute("INSERT INTO lottery_config (id) VALUES (1) ON CONFLICT DO NOTHING")

    op.execute("""
        CREATE TABLE IF NOT EXISTS donations (
            id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            amount_rub INTEGER NOT NULL,
            anticoin_received BIGINT NOT NULL,
            bonus_anticoin BIGINT DEFAULT 0,
            transaction_id TEXT,
            status TEXT DEFAULT 'pending',
            created_at BIGINT NOT NULL,
            completed_at BIGINT DEFAULT 0
        )
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS donation_rates (
            id INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
            rub_per_anticoin INTEGER DEFAULT 10,
            bonus_percent INTEGER DEFAULT 0
        )
    """)

    op.execute("INSERT INTO donation_rates (id) VALUES (1) ON CONFLICT DO NOTHING")

    op.execute("CREATE INDEX IF NOT EXISTS idx_economy_coins ON economy(coins DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_economy_anticoin ON economy(anticoin DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_boosters_user ON boosters(user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_boosters_expires ON boosters(expires_at)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_lottery_user ON lottery(user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_lottery_winner ON lottery(is_winner)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_donations_user ON donations(user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_donations_status ON donations(status)")

    op.execute("""
        CREATE OR REPLACE FUNCTION update_economy_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = EXTRACT(EPOCH FROM NOW());
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """)

    op.execute("DROP TRIGGER IF EXISTS update_economy_updated_at ON economy")
    op.execute("""
        CREATE TRIGGER update_economy_updated_at
            BEFORE UPDATE ON economy
            FOR EACH ROW
            EXECUTE FUNCTION update_economy_updated_at()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS update_economy_updated_at ON economy")
    op.execute("DROP FUNCTION IF EXISTS update_economy_updated_at")
    op.execute("DROP TABLE IF EXISTS donations")
    op.execute("DROP TABLE IF EXISTS donation_rates")
    op.execute("DROP TABLE IF EXISTS lottery")
    op.execute("DROP TABLE IF EXISTS lottery_config")
    op.execute("DROP TABLE IF EXISTS boosters")
    op.execute("DROP TABLE IF EXISTS monthly_rewards_config")
    op.execute("DROP TABLE IF EXISTS weekly_rewards_config")
    op.execute("DROP TABLE IF EXISTS daily_rewards_config")
    op.execute("DROP TABLE IF EXISTS inventory")
    op.execute("DROP TABLE IF EXISTS shop")
    op.execute("DROP TABLE IF EXISTS economy")