from pydantic import BaseModel, Field, ConfigDict, field_validator


def _coerce_int(v):
    if v is None or v == "":
        return 0
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        try:
            return int(v.strip())
        except ValueError:
            return 0
    return 0


def _coerce_int_list(v):
    if v is None:
        return []
    if isinstance(v, list):
        return [_coerce_int(x) for x in v if x not in (None, "", 0, "0")]
    return []


class TagCheckSettings(BaseModel):
    model_config = ConfigDict(extra="allow")

    whitelist_user_ids: list[int] = []
    notify_channel_id: int = 0
    grace_hours: int = 48
    reminder_hours_before: int = 1

    @field_validator("whitelist_user_ids", mode="before")
    @classmethod
    def _wl(cls, v):
        return _coerce_int_list(v)

    @field_validator("notify_channel_id", mode="before")
    @classmethod
    def _ch(cls, v):
        return _coerce_int(v)

    @field_validator("grace_hours", mode="before")
    @classmethod
    def _gh(cls, v):
        try:
            n = int(v) if v else 48
            return max(1, min(n, 720))
        except (TypeError, ValueError):
            return 48

    @field_validator("reminder_hours_before", mode="before")
    @classmethod
    def _rh(cls, v):
        try:
            n = int(v) if v else 1
            return max(0, min(n, 24))
        except (TypeError, ValueError):
            return 1


class StackSettings(BaseModel):
    model_config = ConfigDict(extra="allow")

    applications_channel_id: int = 0
    approval_log_channel_id: int = 0
    archive_category_id: int = 0
    max_voice_channels: int = 8
    max_text_channels: int = 3
    default_voice_count: int = 4
    default_text_count: int = 1
    tag_check: TagCheckSettings = Field(default_factory=TagCheckSettings)

    @field_validator("applications_channel_id", "approval_log_channel_id", "archive_category_id",
                     "max_voice_channels", "max_text_channels", "default_voice_count", "default_text_count",
                     mode="before")
    @classmethod
    def _nums(cls, v):
        return _coerce_int(v)


class ModerationSettings(BaseModel):
    model_config = ConfigDict(extra="allow")

    admin_role_ids: list[int] = []
    mod_log_channel_id: int = 0
    server_log_channel_id: int = 0

    @field_validator("admin_role_ids", mode="before")
    @classmethod
    def _adm(cls, v):
        return _coerce_int_list(v)

    @field_validator("mod_log_channel_id", "server_log_channel_id", mode="before")
    @classmethod
    def _ch(cls, v):
        return _coerce_int(v)


class LogEvent(BaseModel):
    model_config = ConfigDict(extra="allow")
    channel: int = 0
    enabled: bool = True

    @field_validator("channel", mode="before")
    @classmethod
    def _ch(cls, v):
        return _coerce_int(v)


class GiveawayAntiCheat(BaseModel):
    model_config = ConfigDict(extra="allow")
    min_account_age_days: int = 7
    grace_period_seconds: int = 600
    require_avatar_if_young: bool = True
    young_threshold_days: int = 30
    mass_join_detection_window: int = 10
    mass_join_detection_count: int = 5
    check_suspicious_flags: bool = True

    @field_validator(
        "min_account_age_days", "grace_period_seconds", "young_threshold_days",
        "mass_join_detection_window", "mass_join_detection_count",
        mode="before"
    )
    @classmethod
    def _nums(cls, v):
        return _coerce_int(v)


class VoiceStatsSettings(BaseModel):
    model_config = ConfigDict(extra="allow")

    counted_category_ids: list[int] = []
    afk_channel_id: int = 0

    @field_validator("counted_category_ids", mode="before")
    @classmethod
    def _cats(cls, v):
        return _coerce_int_list(v)

    @field_validator("afk_channel_id", mode="before")
    @classmethod
    def _afk(cls, v):
        return _coerce_int(v)


class VoicePrivateSettings(BaseModel):
    model_config = ConfigDict(extra="allow")

    trigger_channel_id: int = 0
    category_id: int = 0

    @field_validator("trigger_channel_id", "category_id", mode="before")
    @classmethod
    def _ids(cls, v):
        return _coerce_int(v)


class ClanTagSettings(BaseModel):
    model_config = ConfigDict(extra="allow")

    role_id: int = 0
    enabled: bool = True
    sync_interval_minutes: int = 5

    @field_validator("role_id", mode="before")
    @classmethod
    def _role(cls, v):
        return _coerce_int(v)

    @field_validator("sync_interval_minutes", mode="before")
    @classmethod
    def _interval(cls, v):
        try:
            n = int(v) if v else 5
            return max(5, min(n, 1440))
        except (TypeError, ValueError):
            return 5


class Settings(BaseModel):
    model_config = ConfigDict(extra="allow")

    guild_id: int = 0
    super_admin_id: int = 0
    admin_role_ids: list[int] = []
    manager_ids: list[int] = []
    stacks: StackSettings = Field(default_factory=StackSettings)
    moderation: ModerationSettings = Field(default_factory=ModerationSettings)
    voice_stats: VoiceStatsSettings = Field(default_factory=VoiceStatsSettings)
    voice_private: VoicePrivateSettings = Field(default_factory=VoicePrivateSettings)
    clan_tag: ClanTagSettings = Field(default_factory=ClanTagSettings)

    @field_validator("guild_id", "super_admin_id", mode="before")
    @classmethod
    def _ids(cls, v):
        return _coerce_int(v)

    @field_validator("admin_role_ids", "manager_ids", mode="before")
    @classmethod
    def _id_lists(cls, v):
        return _coerce_int_list(v)


class RootConfig(BaseModel):
    model_config = ConfigDict(extra="allow")

    settings: Settings = Field(default_factory=Settings)
    logging: dict[str, LogEvent] = {}
    giveaway_anticheat: GiveawayAntiCheat = Field(default_factory=GiveawayAntiCheat)
