CREATE TABLE IF NOT EXISTS user_profiles (
    user_id BIGINT PRIMARY KEY,
    balance BIGINT DEFAULT 0,
    total_earned BIGINT DEFAULT 0,
    total_spent BIGINT DEFAULT 0,
    marry_partner_id BIGINT DEFAULT 0,
    clan_id INTEGER DEFAULT 0,
    reputation INTEGER DEFAULT 0,
    level INTEGER DEFAULT 1,
    exp BIGINT DEFAULT 0,
    voice_online BIGINT DEFAULT 0,
    messages BIGINT DEFAULT 0,
    anticoin BIGINT DEFAULT 0,
    daily_last_claim BIGINT DEFAULT 0,
    daily_streak INTEGER DEFAULT 0,
    bio TEXT DEFAULT '',
    created_at BIGINT DEFAULT 0,
    updated_at BIGINT DEFAULT 0
);

CREATE TABLE IF NOT EXISTS transactions (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    amount BIGINT NOT NULL,
    type TEXT NOT NULL,
    ref TEXT DEFAULT '',
    created_at BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS marriages (
    id SERIAL PRIMARY KEY,
    user1 BIGINT NOT NULL,
    user2 BIGINT NOT NULL,
    married_at BIGINT NOT NULL,
    divorced_at BIGINT DEFAULT 0,
    love_points INTEGER DEFAULT 0,
    balance BIGINT DEFAULT 0,
    plata_until BIGINT DEFAULT 0,
    active BOOLEAN DEFAULT TRUE,
    UNIQUE(user1, user2)
);

CREATE TABLE IF NOT EXISTS marriage_gifts (
    id SERIAL PRIMARY KEY,
    marriage_id INTEGER REFERENCES marriages(id) ON DELETE CASCADE,
    giver BIGINT NOT NULL,
    receiver BIGINT NOT NULL,
    amount BIGINT NOT NULL,
    created_at BIGINT NOT NULL,
    message TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS stacks (
    stack_id SERIAL PRIMARY KEY,
    guild_id BIGINT NOT NULL,
    leader_id BIGINT NOT NULL,
    stack_name TEXT NOT NULL,
    role_id BIGINT DEFAULT 0,
    category_id BIGINT DEFAULT 0,
    status TEXT DEFAULT 'pending_approval',
    level INTEGER DEFAULT 1,
    exp INTEGER DEFAULT 0,
    balance BIGINT DEFAULT 0,
    points BIGINT DEFAULT 0,
    description TEXT DEFAULT '',
    icon_url TEXT DEFAULT '',
    created_at BIGINT NOT NULL,
    approved_at BIGINT DEFAULT 0,
    archive_at BIGINT DEFAULT 0,
    recruitment_open BOOLEAN DEFAULT TRUE,
    updated_at BIGINT DEFAULT 0
);

CREATE TABLE IF NOT EXISTS stack_members (
    stack_id INTEGER REFERENCES stacks(stack_id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL,
    joined_at BIGINT NOT NULL,
    added_by BIGINT DEFAULT 0,
    PRIMARY KEY (stack_id, user_id)
);

CREATE TABLE IF NOT EXISTS stack_applications (
    id SERIAL PRIMARY KEY,
    stack_id INTEGER REFERENCES stacks(stack_id) ON DELETE CASCADE,
    applicant_id BIGINT NOT NULL,
    status TEXT DEFAULT 'pending',
    applied_at BIGINT NOT NULL,
    processed_at BIGINT DEFAULT 0,
    processed_by BIGINT DEFAULT 0,
    rejection_reason TEXT
);

CREATE TABLE IF NOT EXISTS stack_reviewers (
    stack_id INTEGER REFERENCES stacks(stack_id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL,
    added_at BIGINT NOT NULL,
    added_by BIGINT NOT NULL,
    PRIMARY KEY (stack_id, user_id)
);

CREATE TABLE IF NOT EXISTS voice_hours (
    id SERIAL PRIMARY KEY,
    guild_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    day_key TEXT NOT NULL,
    seconds BIGINT DEFAULT 0,
    updated_at BIGINT DEFAULT 0,
    UNIQUE(guild_id, user_id, day_key)
);

CREATE TABLE IF NOT EXISTS stack_voice_hours (
    stack_id INTEGER REFERENCES stacks(stack_id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL,
    seconds BIGINT DEFAULT 0,
    last_update BIGINT DEFAULT 0,
    PRIMARY KEY (stack_id, user_id)
);

CREATE TABLE IF NOT EXISTS private_voice (
    user_id BIGINT PRIMARY KEY,
    name TEXT,
    user_limit INTEGER,
    is_closed BOOLEAN DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS private_voice_bans (
    owner_id BIGINT NOT NULL,
    banned_user_id BIGINT NOT NULL,
    PRIMARY KEY (owner_id, banned_user_id)
);

CREATE TABLE IF NOT EXISTS private_voice_mutes (
    owner_id BIGINT NOT NULL,
    muted_user_id BIGINT NOT NULL,
    PRIMARY KEY (owner_id, muted_user_id)
);

CREATE TABLE IF NOT EXISTS active_voice_channels (
    channel_id BIGINT PRIMARY KEY,
    owner_id BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS clan_tag_state (
    user_id BIGINT PRIMARY KEY,
    is_wearing BOOLEAN DEFAULT FALSE,
    last_changed_at BIGINT DEFAULT 0,
    first_detected_at BIGINT DEFAULT 0
);

CREATE TABLE IF NOT EXISTS clan_tag_scan_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    scan_id TEXT NOT NULL,
    current_index INTEGER DEFAULT 0,
    total INTEGER DEFAULT 0,
    started_at BIGINT DEFAULT 0,
    updated_at BIGINT DEFAULT 0
);

CREATE TABLE IF NOT EXISTS stack_tag_warnings (
    user_id BIGINT PRIMARY KEY,
    stack_id INTEGER NOT NULL,
    warned_at BIGINT DEFAULT 0,
    deadline_at BIGINT DEFAULT 0,
    reminded_dm BOOLEAN DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS bot_config (
    key TEXT PRIMARY KEY,
    data JSONB NOT NULL,
    updated_at BIGINT NOT NULL,
    updated_by BIGINT DEFAULT 0
);

CREATE TABLE IF NOT EXISTS bot_config_history (
    id SERIAL PRIMARY KEY,
    key TEXT NOT NULL,
    data JSONB NOT NULL,
    updated_at BIGINT NOT NULL,
    updated_by BIGINT DEFAULT 0
);

CREATE TABLE IF NOT EXISTS bot_status_msg (
    bot_name TEXT PRIMARY KEY,
    channel_id BIGINT NOT NULL,
    message_id BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS banner_state (
    id INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    enabled BOOLEAN DEFAULT TRUE,
    circle_x INTEGER DEFAULT 0,
    circle_y INTEGER DEFAULT 0,
    circle_r INTEGER DEFAULT 0,
    font_size INTEGER DEFAULT 0,
    last_count INTEGER DEFAULT -1,
    last_updated_at BIGINT DEFAULT 0,
    last_error TEXT
);

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
);

CREATE TABLE IF NOT EXISTS gw_participants (
    message_id TEXT NOT NULL,
    user_id BIGINT NOT NULL,
    PRIMARY KEY (message_id, user_id)
);

CREATE TABLE IF NOT EXISTS gw_invites (
    message_id TEXT NOT NULL,
    inviter_id BIGINT NOT NULL,
    code TEXT NOT NULL,
    UNIQUE(message_id, inviter_id),
    UNIQUE(message_id, code)
);

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
);

CREATE TABLE IF NOT EXISTS historical_joins (
    guild_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    first_joined_at BIGINT DEFAULT 0,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS giveaway_voice_targets (
    message_id TEXT NOT NULL,
    target_id BIGINT NOT NULL,
    PRIMARY KEY (message_id, target_id)
);

CREATE TABLE IF NOT EXISTS user_achievements (
    user_id BIGINT PRIMARY KEY,
    achievements JSONB DEFAULT '{}',
    total_points INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS achievements_list (
    id SERIAL PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    description TEXT NOT NULL,
    reward INTEGER DEFAULT 0,
    required INTEGER NOT NULL,
    category TEXT DEFAULT 'general',
    icon TEXT DEFAULT '🏆'
);

CREATE TABLE IF NOT EXISTS shop_items (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    price BIGINT NOT NULL,
    role_id BIGINT DEFAULT 0,
    type TEXT DEFAULT 'role',
    is_limited BOOLEAN DEFAULT FALSE,
    available_until BIGINT DEFAULT 0,
    created_at BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_inventory (
    user_id BIGINT NOT NULL,
    item_id INTEGER REFERENCES shop_items(id) ON DELETE CASCADE,
    quantity INTEGER DEFAULT 1,
    obtained_at BIGINT NOT NULL,
    PRIMARY KEY (user_id, item_id)
);

CREATE TABLE IF NOT EXISTS cases (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    price BIGINT NOT NULL,
    description TEXT DEFAULT '',
    icon TEXT DEFAULT '📦',
    created_at BIGINT NOT NULL
);

CREATE TABLE IF NOT EXISTS case_items (
    case_id INTEGER REFERENCES cases(id) ON DELETE CASCADE,
    item_name TEXT NOT NULL,
    min_amount BIGINT NOT NULL,
    max_amount BIGINT NOT NULL,
    chance INTEGER NOT NULL,
    PRIMARY KEY (case_id, item_name)
);

CREATE TABLE IF NOT EXISTS case_history (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    case_id INTEGER REFERENCES cases(id),
    prize TEXT NOT NULL,
    amount BIGINT NOT NULL,
    opened_at BIGINT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_user_balance ON user_profiles(balance DESC);
CREATE INDEX IF NOT EXISTS idx_user_level ON user_profiles(level DESC);
CREATE INDEX IF NOT EXISTS idx_transactions_user ON transactions(user_id);
CREATE INDEX IF NOT EXISTS idx_marriages_active ON marriages(active);
CREATE INDEX IF NOT EXISTS idx_voice_user ON voice_hours(user_id);
CREATE INDEX IF NOT EXISTS idx_stacks_status ON stacks(status);
CREATE INDEX IF NOT EXISTS idx_stacks_leader ON stacks(leader_id);
CREATE INDEX IF NOT EXISTS idx_giveaways_ended ON giveaways(ended, end_time);
CREATE INDEX IF NOT EXISTS idx_giveaways_guild ON giveaways(guild_id);
CREATE INDEX IF NOT EXISTS idx_clan_wearing ON clan_tag_state(is_wearing) WHERE is_wearing = TRUE;
CREATE INDEX IF NOT EXISTS idx_tag_deadline ON stack_tag_warnings(deadline_at);

CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = EXTRACT(EPOCH FROM NOW());
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS update_stacks_updated_at ON stacks;
CREATE TRIGGER update_stacks_updated_at
    BEFORE UPDATE ON stacks
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at();

DROP TRIGGER IF EXISTS update_user_profiles_updated_at ON user_profiles;
CREATE TRIGGER update_user_profiles_updated_at
    BEFORE UPDATE ON user_profiles
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at();

INSERT INTO achievements_list (name, description, reward, required, category, icon) VALUES
    ('Первые шаги', 'Напишите 30 сообщений или просидите 5 минут в войсе', 50, 30, 'general', '👣'),
    ('Кто-то сказал БУСТЕР?', 'Поддержите проект с помощью буста', 1000, 1, 'booster', '💎'),
    ('Бесплатно?!', 'Заберите ежедневные награды 5 раз', 150, 5, 'daily', '🎁'),
    ('День сурка', 'Заберите ежедневные награды 15 раз', 250, 15, 'daily', '🔄'),
    ('Свой среди своих', 'Отправьте 100 сообщений', 100, 100, 'messages', '💬'),
    ('Голосовой первопроходец', 'Просидите 15 минут в войсе', 100, 900, 'voice', '🎤'),
    ('Узы вечной любви', 'Создать брак', 500, 1, 'marriage', '💍'),
    ('Клановый союзник', 'Вступить в клан', 300, 1, 'clan', '🏰')
ON CONFLICT (name) DO NOTHING;

INSERT INTO banner_state (id) VALUES (1) ON CONFLICT DO NOTHING;