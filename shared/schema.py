async def ensure_core_schema(conn):
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS stacks (
            stack_id SERIAL PRIMARY KEY,
            guild_id BIGINT NOT NULL,
            leader_id BIGINT NOT NULL,
            stack_name TEXT NOT NULL,
            role_id BIGINT DEFAULT 0,
            category_id BIGINT DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending_approval',
            created_at BIGINT NOT NULL,
            approved_at BIGINT DEFAULT 0,
            archive_at BIGINT DEFAULT 0,
            recruitment_open BOOLEAN DEFAULT TRUE
        )
    """)
    await conn.execute("ALTER TABLE stacks ADD COLUMN IF NOT EXISTS recruitment_open BOOLEAN DEFAULT TRUE")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_stacks_guild_status ON stacks(guild_id, status)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_stacks_leader ON stacks(leader_id, status)")
    await conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uniq_leader_active
        ON stacks(leader_id)
        WHERE status IN ('pending_approval', 'active')
    """)
    await conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS uniq_name_active
        ON stacks(guild_id, lower(stack_name))
        WHERE status IN ('pending_approval', 'active')
    """)

    await conn.execute("""
        CREATE TABLE IF NOT EXISTS stack_channels (
            id SERIAL PRIMARY KEY,
            stack_id INTEGER NOT NULL REFERENCES stacks(stack_id) ON DELETE CASCADE,
            channel_id BIGINT NOT NULL,
            channel_type TEXT NOT NULL,
            position INTEGER DEFAULT 0,
            UNIQUE(channel_id)
        )
    """)
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_channels_stack ON stack_channels(stack_id)")

    await conn.execute("""
        CREATE TABLE IF NOT EXISTS stack_members (
            id SERIAL PRIMARY KEY,
            stack_id INTEGER NOT NULL REFERENCES stacks(stack_id) ON DELETE CASCADE,
            user_id BIGINT NOT NULL,
            joined_at BIGINT NOT NULL,
            added_by BIGINT DEFAULT 0,
            UNIQUE(stack_id, user_id)
        )
    """)
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_members_user ON stack_members(user_id)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_members_stack ON stack_members(stack_id)")

    await conn.execute("""
        CREATE TABLE IF NOT EXISTS stack_applications (
            id SERIAL PRIMARY KEY,
            stack_id INTEGER NOT NULL REFERENCES stacks(stack_id) ON DELETE CASCADE,
            applicant_id BIGINT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            leader_msg_id BIGINT DEFAULT 0,
            applied_at BIGINT NOT NULL,
            processed_at BIGINT DEFAULT 0,
            processed_by BIGINT DEFAULT 0,
            rejection_reason TEXT
        )
    """)
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_apps_stack ON stack_applications(stack_id, status)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_apps_applicant ON stack_applications(applicant_id, status)")

    await conn.execute("""
        CREATE TABLE IF NOT EXISTS stack_reviewers (
            id SERIAL PRIMARY KEY,
            stack_id INTEGER NOT NULL REFERENCES stacks(stack_id) ON DELETE CASCADE,
            user_id BIGINT NOT NULL,
            added_at BIGINT NOT NULL,
            added_by BIGINT NOT NULL,
            UNIQUE (stack_id, user_id)
        )
    """)
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_reviewers_stack ON stack_reviewers(stack_id)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_reviewers_user ON stack_reviewers(user_id)")

    await conn.execute("""
        CREATE TABLE IF NOT EXISTS stack_application_messages (
            id SERIAL PRIMARY KEY,
            application_id INTEGER NOT NULL REFERENCES stack_applications(id) ON DELETE CASCADE,
            recipient_id BIGINT NOT NULL,
            channel_id BIGINT NOT NULL,
            message_id BIGINT NOT NULL,
            UNIQUE (application_id, recipient_id)
        )
    """)
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_app_msgs_app ON stack_application_messages(application_id)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_app_msgs_msgid ON stack_application_messages(message_id)")

    await conn.execute("""
        CREATE TABLE IF NOT EXISTS stack_log_msgs (
            stack_id INTEGER PRIMARY KEY REFERENCES stacks(stack_id) ON DELETE CASCADE,
            channel_id BIGINT NOT NULL,
            message_id BIGINT NOT NULL
        )
    """)

    await conn.execute("""
        CREATE TABLE IF NOT EXISTS applications_panel (
            guild_id BIGINT PRIMARY KEY,
            channel_id BIGINT NOT NULL,
            message_id BIGINT NOT NULL,
            updated_at BIGINT NOT NULL
        )
    """)

    await conn.execute("""
        CREATE TABLE IF NOT EXISTS bot_status_msg (
            bot_name TEXT PRIMARY KEY,
            channel_id BIGINT NOT NULL,
            message_id BIGINT NOT NULL
        )
    """)

    await conn.execute("""
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
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_gw_ended_end ON giveaways(ended, end_time)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_gw_guild ON giveaways(guild_id)")

    await conn.execute("""
        CREATE TABLE IF NOT EXISTS gw_participants (
            message_id TEXT NOT NULL,
            user_id BIGINT NOT NULL,
            UNIQUE(message_id, user_id)
        )
    """)
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_gw_p_msg ON gw_participants(message_id)")

    await conn.execute("""
        CREATE TABLE IF NOT EXISTS gw_invites (
            message_id TEXT NOT NULL,
            inviter_id BIGINT NOT NULL,
            code TEXT NOT NULL,
            UNIQUE(message_id, inviter_id),
            UNIQUE(message_id, code)
        )
    """)
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_gw_inv_code ON gw_invites(code)")

    await conn.execute("""
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
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_gwij_msg_inv ON gw_invite_joins(message_id, inviter_id)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_gwij_status ON gw_invite_joins(message_id, status)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_gwij_joined_at ON gw_invite_joins(joined_at)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_gwij_joined_user ON gw_invite_joins(joined_user_id)")

    await conn.execute("""
        CREATE TABLE IF NOT EXISTS historical_joins (
            guild_id BIGINT NOT NULL,
            user_id BIGINT NOT NULL,
            first_joined_at BIGINT DEFAULT 0,
            UNIQUE(guild_id, user_id)
        )
    """)

    await conn.execute("""
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
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_vh_guild_day ON voice_hours(guild_id, day_key)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_vh_user ON voice_hours(user_id)")

    await conn.execute("""
        CREATE TABLE IF NOT EXISTS private_voice (
            user_id BIGINT PRIMARY KEY,
            name TEXT,
            user_limit INTEGER,
            is_closed BOOLEAN DEFAULT FALSE
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS private_voice_bans (
            owner_id BIGINT NOT NULL,
            banned_user_id BIGINT NOT NULL,
            UNIQUE(owner_id, banned_user_id)
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS private_voice_mutes (
            owner_id BIGINT NOT NULL,
            muted_user_id BIGINT NOT NULL,
            UNIQUE(owner_id, muted_user_id)
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS active_voice_channels (
            channel_id BIGINT PRIMARY KEY,
            owner_id BIGINT NOT NULL
        )
    """)

    await conn.execute("""
        CREATE TABLE IF NOT EXISTS clan_tag_state (
            user_id BIGINT PRIMARY KEY,
            is_wearing BOOLEAN NOT NULL DEFAULT FALSE,
            last_changed_at BIGINT NOT NULL DEFAULT 0,
            first_detected_at BIGINT NOT NULL DEFAULT 0
        )
    """)
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_cts_wearing ON clan_tag_state(is_wearing) WHERE is_wearing = TRUE")

    await conn.execute("""
        CREATE TABLE IF NOT EXISTS clan_tag_scan_state (
            id INTEGER PRIMARY KEY,
            scan_id TEXT NOT NULL,
            current_index INTEGER NOT NULL DEFAULT 0,
            total INTEGER NOT NULL DEFAULT 0,
            started_at BIGINT NOT NULL DEFAULT 0,
            updated_at BIGINT NOT NULL DEFAULT 0
        )
    """)

    await conn.execute("""
        CREATE TABLE IF NOT EXISTS giveaway_voice_targets (
            message_id TEXT NOT NULL,
            target_id BIGINT NOT NULL,
            PRIMARY KEY (message_id, target_id)
        )
    """)
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_gwvt_message ON giveaway_voice_targets(message_id)")

    await conn.execute("""
        CREATE TABLE IF NOT EXISTS stack_tag_warnings (
            user_id BIGINT PRIMARY KEY,
            stack_id INTEGER NOT NULL,
            warned_at BIGINT NOT NULL DEFAULT 0,
            deadline_at BIGINT NOT NULL DEFAULT 0,
            reminded_dm BOOLEAN NOT NULL DEFAULT FALSE
        )
    """)
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_stw_deadline ON stack_tag_warnings(deadline_at)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_stw_stack ON stack_tag_warnings(stack_id)")

    await conn.execute("""
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
    await conn.execute("INSERT INTO banner_state (id) VALUES (1) ON CONFLICT DO NOTHING")
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS user_profiles (
            user_id BIGINT PRIMARY KEY,
            balance BIGINT DEFAULT 0,
            total_earned BIGINT DEFAULT 0,
            total_spent BIGINT DEFAULT 0,
            marry_partner_id BIGINT DEFAULT 0,
            clan_id INTEGER DEFAULT 0,
            join_date BIGINT DEFAULT 0,
            bio TEXT DEFAULT '',
            background_url TEXT DEFAULT '',
            reputation INTEGER DEFAULT 0,
            created_at BIGINT NOT NULL DEFAULT 0
        )
    """)

    await conn.execute("""
        CREATE TABLE IF NOT EXISTS clans (
            clan_id SERIAL PRIMARY KEY,
            clan_name TEXT UNIQUE NOT NULL,
            clan_tag TEXT UNIQUE NOT NULL,
            owner_id BIGINT NOT NULL,
            balance BIGINT DEFAULT 0,
            level INTEGER DEFAULT 1,
            exp INTEGER DEFAULT 0,
            created_at BIGINT NOT NULL,
            description TEXT DEFAULT '',
            icon_url TEXT DEFAULT '',
            banner_url TEXT DEFAULT '',
            members_count INTEGER DEFAULT 1
        )
    """)

    await conn.execute("""
        CREATE TABLE IF NOT EXISTS clan_members (
            clan_id INTEGER NOT NULL REFERENCES clans(clan_id) ON DELETE CASCADE,
            user_id BIGINT NOT NULL,
            role TEXT DEFAULT 'member',
            joined_at BIGINT NOT NULL,
            contribution BIGINT DEFAULT 0,
            PRIMARY KEY (clan_id, user_id)
        )
    """)

    await conn.execute("""
        CREATE TABLE IF NOT EXISTS clan_invites (
            clan_id INTEGER NOT NULL REFERENCES clans(clan_id) ON DELETE CASCADE,
            user_id BIGINT NOT NULL,
            invited_by BIGINT NOT NULL,
            created_at BIGINT NOT NULL,
            expires_at BIGINT NOT NULL,
            PRIMARY KEY (clan_id, user_id)
        )
    """)

    await conn.execute("""
        CREATE TABLE IF NOT EXISTS marriages (
            marriage_id SERIAL PRIMARY KEY,
            user1_id BIGINT NOT NULL,
            user2_id BIGINT NOT NULL,
            married_at BIGINT NOT NULL,
            divorced_at BIGINT DEFAULT 0,
            proposer_id BIGINT NOT NULL,
            love_points INTEGER DEFAULT 0,
            is_active BOOLEAN DEFAULT TRUE,
            UNIQUE(user1_id, user2_id)
        )
    """)

    await conn.execute("""
        CREATE TABLE IF NOT EXISTS marriage_gifts (
            gift_id SERIAL PRIMARY KEY,
            marriage_id INTEGER NOT NULL REFERENCES marriages(marriage_id) ON DELETE CASCADE,
            giver_id BIGINT NOT NULL,
            receiver_id BIGINT NOT NULL,
            amount BIGINT NOT NULL,
            gift_at BIGINT NOT NULL,
            message TEXT DEFAULT ''
        )
    """)

    await conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_rewards (
            user_id BIGINT PRIMARY KEY,
            last_claim BIGINT DEFAULT 0,
            streak INTEGER DEFAULT 0
        )
    """)

    await conn.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            transaction_id SERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            amount BIGINT NOT NULL,
            type TEXT NOT NULL,
            reference_id TEXT DEFAULT '',
            created_at BIGINT NOT NULL
        )
    """)
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_transactions_user ON transactions(user_id)")


REJECT_BOT = "Бот-аккаунт"
REJECT_SELF = "Самоприглашение (инвайтер и приглашённый совпадают)"
REJECT_OLD_MEMBER = "Повторный вход (пользователь уже был на сервере)"
REJECT_YOUNG_ACCOUNT = "Аккаунт младше требуемого порога"
REJECT_ACCOUNT_CREATED_AFTER_GW = "Аккаунт создан после старта розыгрыша"
REJECT_NO_AVATAR = "Молодой аккаунт без аватарки"
REJECT_LEFT = "Покинул сервер"
REJECT_LEFT_TOO_FAST = "Покинул сервер слишком быстро (меньше 10 минут)"
REJECT_MANUAL = "Удалено администратором"
REJECT_SUSPICIOUS_FLAGS = "Подозрительный аккаунт (флаги Discord)"
REJECT_MASS_JOIN = "Массовое вступление (возможный рейд)"
REJECT_DUPLICATE_INVITER = "Уже принёс очки другому инвайтеру"

STATUS_VALID = "valid"
STATUS_INVALID = "invalid"
STATUS_PENDING = "pending"
