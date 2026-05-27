import disnake
from disnake.ext import commands
import sys

sys.path.insert(0, "/root/antisocial")
from shared.config_manager import load_config as _load_typed, save_config as _save_typed
from shared.config_models import RootConfig
from shared.staff import is_staff


async def load_config():
    cfg = await _load_typed()
    return cfg.model_dump()


async def save_config(data, updated_by=0):
    try:
        new_cfg = RootConfig(**data)
    except Exception:
        new_cfg = RootConfig.model_construct(**data)
    await _save_typed(new_cfg, updated_by=updated_by)


COLOR_BG = 0x2b2d31
COLOR_OK = 0x9EE5B4
COLOR_ERR = 0xF6C4C5


def err_embed(desc):
    return disnake.Embed(title="—・Ошибка", description=desc, color=COLOR_ERR)


LOG_CATEGORIES = {
    "members": ("Участники и Сервер", "👥", {
        "member_join": "Вход участника",
        "member_leave": "Выход участника",
        "bot_add": "Добавление бота",
        "member_ban": "Блокировка участника",
        "member_unban": "Разблокировка участника",
        "member_nick_update": "Изменение имени",
        "guild_update": "Изменение сервера",
    }),
    "voice": ("Голосовые каналы", "🔊", {
        "voice_join": "Вход в голосовой канал",
        "voice_leave": "Выход из голосового канала",
        "voice_move": "Переход из канала",
        "voice_mute": "Выключение микрофона",
        "voice_unmute": "Включение микрофона",
        "voice_deafen": "Выключение наушников",
        "voice_undeafen": "Включение наушников",
    }),
    "messages": ("Сообщения", "💬", {
        "message_edit": "Изменение сообщения",
        "message_delete": "Удаление сообщения",
    }),
    "channels": ("Каналы и Ветки", "📁", {
        "channel_create": "Создание канала",
        "channel_update": "Изменение канала",
        "channel_delete": "Удаление канала",
        "thread_create": "Создание ветки",
        "thread_update": "Изменение ветки",
        "thread_delete": "Удаление ветки",
    }),
    "roles": ("Роли", "🎭", {
        "role_create": "Создание роли",
        "role_update": "Изменение роли",
        "role_delete": "Удаление роли",
        "member_role_add": "Добавление ролей участнику",
        "member_role_remove": "Удаление ролей участнику",
    }),
    "other": ("Прочее", "⚙️", {
        "emoji_create": "Создание эмодзи",
        "emoji_update": "Изменение эмодзи",
        "emoji_delete": "Удаление эмодзи",
        "sticker_create": "Создание стикера",
        "sticker_update": "Изменение стикера",
        "sticker_delete": "Удаление стикера",
        "invite_create": "Создание приглашения",
        "invite_delete": "Удаление приглашения",
        "event_create": "Создание мероприятия",
        "event_update": "Изменение мероприятия",
        "event_delete": "Удаление мероприятия",
        "webhook_create": "Создание вебхука",
        "webhook_update": "Изменение вебхука",
        "webhook_delete": "Удаление вебхука",
    }),
    "clan_tag": ("Клан-тег", "🏷️", {
        "clan_tag_added": "Тег надет (юзер начал носить)",
        "clan_tag_removed": "Тег снят",
    }),
}


async def get_log_system_embed():
    data = await load_config()
    log_cfg = data.get("logging", {})
    srv_id = data.get("settings", {}).get("moderation", {}).get("server_log_channel_id")
    srv = f"<#{srv_id}>" if srv_id else "*не установлен*"

    total_events = sum(len(events) for _, _, events in LOG_CATEGORIES.values())
    total_enabled = sum(
        1 for cat_key, (_, _, events) in LOG_CATEGORIES.items()
        for ev_key in events
        if log_cfg.get(ev_key, {}).get("enabled", False)
    )
    pct = round(100 * total_enabled / total_events) if total_events else 0
    bar = _progress_bar(pct)

    embed = disnake.Embed(
        title="📋 Система логирования",
        description=(
            f"**Общий канал-fallback:** {srv}\n"
            f"*Если у события не задан свой канал — лог пишется в этот.*\n\n"
            f"**Активно событий:** `{total_enabled} / {total_events}` ({pct}%)\n"
            f"{bar}\n\n"
            f"**Выберите категорию для настройки:**"
        ),
        color=COLOR_BG
    )

    for cat_key, (cat_name, emoji, events) in LOG_CATEGORIES.items():
        cat_enabled = sum(1 for ev_key in events if log_cfg.get(ev_key, {}).get("enabled", False))
        cat_total = len(events)
        lines = []
        for ev_key, ev_name in events.items():
            is_on = log_cfg.get(ev_key, {}).get("enabled", False)
            has_own_channel = log_cfg.get(ev_key, {}).get("channel", 0) > 0
            channel_marker = " 📍" if has_own_channel else ""
            lines.append(f"{'🟢' if is_on else '⚫'} {ev_name}{channel_marker}")

        field_value = "\n".join(lines)
        embed.add_field(
            name=f"{emoji} {cat_name} ({cat_enabled}/{cat_total})",
            value=field_value,
            inline=True
        )

    embed.set_footer(text="🟢 включено  ⚫ выключено  📍 свой канал")
    return embed


def _progress_bar(percent, width=20):
    filled = round(width * percent / 100)
    bar = "█" * filled + "░" * (width - filled)
    return f"`[{bar}]`"


async def get_log_event_embed(event_key, event_name):
    data = await load_config()
    log_cfg = data.get("logging", {}).get(event_key, {})
    is_enabled = log_cfg.get("enabled", False)
    state_emoji = "🟢" if is_enabled else "⚫"
    state_text = "включён" if is_enabled else "выключен"

    spec_ch = log_cfg.get("channel")
    if spec_ch:
        ch_text = f"<#{spec_ch}>"
        ch_source = "📍 *индивидуальный канал*"
    else:
        def_ch = data.get("settings", {}).get("moderation", {}).get("server_log_channel_id")
        if def_ch:
            ch_text = f"<#{def_ch}>"
            ch_source = "🌐 *общий канал-fallback*"
        else:
            ch_text = "*не установлен*"
            ch_source = "⚠️ *логи пропадают — настройте общий канал*"

    return disnake.Embed(
        title=f"📝 Настройка: {event_name}",
        description=(
            f"**Статус:** {state_emoji} `{state_text}`\n"
            f"**Канал:** {ch_text}\n{ch_source}\n\n"
            f"*Используйте кнопки ниже:*\n"
            f"\u200b**・** **Переключить** — включить/выключить событие\n"
            f"\u200b**・** **Канал** — задать индивидуальный канал (или сбросить — оставив пустое значение)\n"
            f"\u200b**・** **Назад** — вернуться к списку"
        ),
        color=COLOR_BG
    )


async def build_main_embed(guild):
    data = await load_config()
    s = data.get("settings", {})
    ss = s.get("stacks", {})
    vs = s.get("voice_stats", {})
    vp = s.get("voice_private", {})
    ct = s.get("clan_tag", {})
    log_cfg = data.get("logging", {})

    super_admin = f"<@{s.get('super_admin_id')}>" if s.get('super_admin_id') else "*не задан*"
    manager_ids = s.get("manager_ids", [])
    managers = ", ".join(f"<@{m}>" for m in manager_ids) if manager_ids else "*нет*"
    admin_role_ids = s.get("admin_role_ids", [])
    admin_roles = ", ".join(f"<@&{r}>" for r in admin_role_ids) if admin_role_ids else "*нет*"

    approval = f"<#{ss.get('approval_log_channel_id')}>" if ss.get('approval_log_channel_id') else "*не задан*"
    apps = f"<#{ss.get('applications_channel_id')}>" if ss.get('applications_channel_id') else "*не задан*"
    archive_cat_id = ss.get('archive_category_id')
    archive = f"`{archive_cat_id}`" if archive_cat_id else "*не задана*"

    dv = ss.get("default_voice_count", 4)
    mv = ss.get("max_voice_channels", 8)
    dt = ss.get("default_text_count", 1)
    mt = ss.get("max_text_channels", 3)

    counted = vs.get("counted_category_ids", [])
    cats_short = (
        f"**{len(counted)}** категори" +
        ("я" if len(counted) == 1 else "и" if 2 <= len(counted) <= 4 else "й")
    ) if counted else "*все*"
    afk = f"<#{vs.get('afk_channel_id')}>" if vs.get('afk_channel_id') else "*не задан*"

    vp_trigger = f"<#{vp.get('trigger_channel_id')}>" if vp.get('trigger_channel_id') else "*не задан*"
    vp_cat = f"<#{vp.get('category_id')}>" if vp.get('category_id') else "*не задана*"

    ct_role = f"<@&{ct.get('role_id')}>" if ct.get('role_id') else "*не задана*"
    ct_enabled = "🟢 вкл" if ct.get("enabled", True) else "🔴 выкл"
    ct_interval = ct.get("sync_interval_minutes", 15)

    tc = ss.get("tag_check", {})
    tc_wl = tc.get("whitelist_user_ids", [])
    tc_notify = f"<#{tc.get('notify_channel_id')}>" if tc.get('notify_channel_id') else "*не задан*"
    tc_grace = tc.get("grace_hours", 48)
    tc_remind = tc.get("reminder_hours_before", 1)

    total_log_events = sum(len(events) for _, _, events in LOG_CATEGORIES.values())
    enabled_log_events = sum(
        1 for _, (_, _, events) in LOG_CATEGORIES.items()
        for ev_key in events
        if log_cfg.get(ev_key, {}).get("enabled", False)
    )
    srv_log = s.get("moderation", {}).get("server_log_channel_id")
    srv_log_text = f"<#{srv_log}>" if srv_log else "*не задан*"

    embed = disnake.Embed(
        title="⚙️  Панель настроек • Antisocial",
        description=(
            "Управление конфигурацией бота. Выберите раздел в меню ниже.\n"
            "Все изменения применяются мгновенно и синхронизируются между процессами."
        ),
        color=COLOR_BG
    )

    embed.add_field(
        name="🏠  Доступ к боту",
        value=(
            f"\u200b**・** Супер-админ: {super_admin}\n"
            f"\u200b**・** Менеджеры: {managers}\n"
            f"\u200b**・** Админ-роли: {admin_roles}"
        ),
        inline=False
    )

    embed.add_field(
        name="📦  Стаки — каналы",
        value=(
            f"\u200b**・** Лог одобрений: {approval}\n"
            f"\u200b**・** Канал заявок: {apps}\n"
            f"\u200b**・** Архив-категория: {archive}"
        ),
        inline=False
    )

    embed.add_field(
        name="⚙️  Стаки — лимиты",
        value=(
            f"\u200b**・** Голосовых: **{dv}** по умолчанию · **{mv}** максимум\n"
            f"\u200b**・** Текстовых: **{dt}** по умолчанию · **{mt}** максимум"
        ),
        inline=False
    )

    embed.add_field(
        name="📋  Логирование",
        value=(
            f"\u200b**・** Активно событий: **{enabled_log_events} / {total_log_events}**\n"
            f"\u200b**・** Общий канал: {srv_log_text}"
        ),
        inline=False
    )

    embed.add_field(
        name="🎤  Voice Stats",
        value=(
            f"\u200b**・** Засчитываемые категории: {cats_short}\n"
            f"\u200b**・** AFK канал: {afk}"
        ),
        inline=False
    )

    embed.add_field(
        name="🔒  Voice Private",
        value=(
            f"\u200b**・** Триггер-канал: {vp_trigger}\n"
            f"\u200b**・** Категория приваток: {vp_cat}"
        ),
        inline=False
    )

    embed.add_field(
        name="🏷️  Клан-тег",
        value=(
            f"\u200b**・** Авто-синхронизация: {ct_enabled}\n"
            f"\u200b**・** Роль для носителей: {ct_role}\n"
            f"\u200b**・** Интервал проверки: **{ct_interval}** мин"
        ),
        inline=False
    )

    embed.add_field(
        name="🏷️  Стаки: проверка тега",
        value=(
            f"\u200b**・** Канал уведомлений: {tc_notify}\n"
            f"\u200b**・** Срок на постановку: **{tc_grace}** ч.\n"
            f"\u200b**・** DM-напоминание за: **{tc_remind}** ч.\n"
            f"\u200b**・** Whitelist (исключения): **{len(tc_wl)}** чел."
        ),
        inline=False
    )

    if guild and guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.set_footer(text="Antisocial • Settings panel")
    return embed


async def build_main_view(author_id):
    view = MainSettingsView(author_id)
    return view


class MainSettingsView(disnake.ui.View):
    def __init__(self, author_id):
        super().__init__(timeout=300)
        self.author_id = author_id

    async def interaction_check(self, inter):
        if inter.author.id != self.author_id:
            await inter.response.send_message(embed=err_embed("Эта панель не для вас."), ephemeral=True)
            return False
        return True

    @disnake.ui.string_select(placeholder="Выберите раздел", options=[
        disnake.SelectOption(label="Основное (админы, менеджеры)", value="main", emoji="🏠"),
        disnake.SelectOption(label="Стаки: каналы", value="channels", emoji="📦"),
        disnake.SelectOption(label="Параметры стаков", value="params", emoji="⚙️"),
        disnake.SelectOption(label="Стаки: проверка тега", value="tc", emoji="🏷️"),
        disnake.SelectOption(label="Логирование", value="logs", emoji="📋"),
        disnake.SelectOption(label="Voice Stats (часы в голосе)", value="vs", emoji="🎤"),
        disnake.SelectOption(label="Voice Private (приватки)", value="vp", emoji="🔒"),
        disnake.SelectOption(label="Клан-тег (роль за тег гильдии)", value="ct", emoji="🏷️"),
        disnake.SelectOption(label="Просмотр полного конфига", value="view", emoji="👀"),
    ])
    async def select_section(self, select, inter):
        val = select.values[0]
        if val == "main":
            await inter.response.edit_message(embed=await build_basic_embed(), view=BasicView(self))
        elif val == "channels":
            await inter.response.edit_message(embed=await build_channels_embed(), view=ChannelsView(self))
        elif val == "params":
            await inter.response.edit_message(embed=await build_params_embed(), view=ParamsView(self))
        elif val == "tc":
            await inter.response.edit_message(embed=await build_tc_embed(), view=TagCheckView(self))
        elif val == "logs":
            await inter.response.edit_message(embed=await get_log_system_embed(), view=LogsView(self))
        elif val == "vs":
            await inter.response.edit_message(embed=await build_vs_embed(), view=VoiceStatsView(self))
        elif val == "vp":
            await inter.response.edit_message(embed=await build_vp_embed(), view=VoicePrivateView(self))
        elif val == "ct":
            await inter.response.edit_message(embed=await build_ct_embed(), view=ClanTagView(self))
        elif val == "view":
            await inter.response.edit_message(embed=await build_fullview_embed(), view=FullviewView(self))

    async def restore(self, inter):
        guild = inter.guild
        embed = await build_main_embed(guild)
        await inter.response.edit_message(embed=embed, view=self)


async def build_basic_embed():
    data = await load_config()
    s = data.get("settings", {})
    super_admin = f"<@{s.get('super_admin_id')}>" if s.get('super_admin_id') else "Не задан"
    manager_ids = s.get("manager_ids", [])
    managers = ", ".join(f"<@{m}>" for m in manager_ids) if manager_ids else "Нет"
    admin_role_ids = s.get("admin_role_ids", [])
    admin_roles = ", ".join(f"<@&{r}>" for r in admin_role_ids) if admin_role_ids else "Нет"
    embed = disnake.Embed(title="—・Основное", color=COLOR_BG)
    embed.description = (
        f"**Текущие параметры:**\n"
        f"\u200b**・** Супер-админ: {super_admin}\n"
        f"\u200b**・** Менеджеры: {managers}\n"
        f"\u200b**・** Админ-роли: {admin_roles}\n\n"
        f"*Менеджеры — те, кто могут пользоваться /настройки.*\n"
        f"*Админ-роли — те, чьи обладатели могут одобрять/отклонять стаки.*"
    )
    return embed


class BasicView(disnake.ui.View):
    def __init__(self, parent):
        super().__init__(timeout=300)
        self.parent = parent

    async def interaction_check(self, inter):
        return await self.parent.interaction_check(inter)

    @disnake.ui.user_select(placeholder="Супер-админ (один)", min_values=1, max_values=1, row=0)
    async def sel_super_admin(self, select, inter):
        data = await load_config()
        data.setdefault("settings", {})["super_admin_id"] = select.values[0].id
        await save_config(data, updated_by=inter.author.id)
        await inter.response.edit_message(embed=await build_basic_embed(), view=self)

    @disnake.ui.user_select(placeholder="Менеджеры (тоггл)", min_values=1, max_values=10, row=1)
    async def sel_managers(self, select, inter):
        data = await load_config()
        managers = data.setdefault("settings", {}).get("manager_ids", [])
        super_admin = data["settings"].get("super_admin_id")
        for u in select.values:
            if u.id == super_admin:
                continue
            if u.id in managers:
                managers.remove(u.id)
            else:
                managers.append(u.id)
        data["settings"]["manager_ids"] = managers
        await save_config(data, updated_by=inter.author.id)
        await inter.response.edit_message(embed=await build_basic_embed(), view=self)

    @disnake.ui.role_select(placeholder="Админ-роли (выбрать заново)", min_values=0, max_values=10, row=2)
    async def sel_admin_roles(self, select, inter):
        data = await load_config()
        data.setdefault("settings", {})["admin_role_ids"] = [r.id for r in select.values] if select.values else []
        await save_config(data, updated_by=inter.author.id)
        await inter.response.edit_message(embed=await build_basic_embed(), view=self)

    @disnake.ui.button(label="Назад", style=disnake.ButtonStyle.grey, emoji="↩️", row=3)
    async def back(self, button, inter):
        await self.parent.restore(inter)


async def build_channels_embed():
    data = await load_config()
    ss = data.get("settings", {}).get("stacks", {})
    approval = f"<#{ss.get('approval_log_channel_id')}>" if ss.get('approval_log_channel_id') else "Не задан"
    apps = f"<#{ss.get('applications_channel_id')}>" if ss.get('applications_channel_id') else "Не задан"
    archive = f"<#{ss.get('archive_category_id')}>" if ss.get('archive_category_id') else "Не задана"
    embed = disnake.Embed(title="—・Стаки: каналы", color=COLOR_BG)
    embed.description = (
        f"**Текущие параметры:**\n"
        f"\u200b**・** Лог одобрений: {approval}\n"
        f"\u200b**・** Канал заявок (где кнопки): {apps}\n"
        f"\u200b**・** Категория архива: {archive}\n\n"
        f"*Лог одобрений — куда бот отправляет новые заявки на создание стака.*\n"
        f"*Канал заявок — где висит сообщение с кнопками «Подать заявку в X».*\n"
        f"*Архив — сюда переносятся каналы удалённых стаков на 30 дней.*"
    )
    return embed


class ChannelsView(disnake.ui.View):
    def __init__(self, parent):
        super().__init__(timeout=300)
        self.parent = parent

    async def interaction_check(self, inter):
        return await self.parent.interaction_check(inter)

    @disnake.ui.channel_select(placeholder="Канал одобрений", channel_types=[disnake.ChannelType.text], min_values=0, max_values=1, row=0)
    async def sel_approval(self, select, inter):
        data = await load_config()
        data.setdefault("settings", {}).setdefault("stacks", {})["approval_log_channel_id"] = select.values[0].id if select.values else 0
        await save_config(data, updated_by=inter.author.id)
        await inter.response.edit_message(embed=await build_channels_embed(), view=self)

    @disnake.ui.channel_select(placeholder="Канал #заявки", channel_types=[disnake.ChannelType.text], min_values=0, max_values=1, row=1)
    async def sel_apps(self, select, inter):
        data = await load_config()
        data.setdefault("settings", {}).setdefault("stacks", {})["applications_channel_id"] = select.values[0].id if select.values else 0
        await save_config(data, updated_by=inter.author.id)
        await inter.response.edit_message(embed=await build_channels_embed(), view=self)

    @disnake.ui.channel_select(placeholder="Категория архива", channel_types=[disnake.ChannelType.category], min_values=0, max_values=1, row=2)
    async def sel_archive(self, select, inter):
        data = await load_config()
        data.setdefault("settings", {}).setdefault("stacks", {})["archive_category_id"] = select.values[0].id if select.values else 0
        await save_config(data, updated_by=inter.author.id)
        await inter.response.edit_message(embed=await build_channels_embed(), view=self)

    @disnake.ui.button(label="Назад", style=disnake.ButtonStyle.grey, emoji="↩️", row=3)
    async def back(self, button, inter):
        await self.parent.restore(inter)


async def build_params_embed():
    data = await load_config()
    ss = data.get("settings", {}).get("stacks", {})
    dv = ss.get("default_voice_count", 4)
    mv = ss.get("max_voice_channels", 8)
    dt = ss.get("default_text_count", 1)
    mt = ss.get("max_text_channels", 3)
    embed = disnake.Embed(title="—・Параметры стаков", color=COLOR_BG)
    embed.description = (
        f"**Текущие параметры:**\n"
        f"\u200b**・** 🎙️ Войсов по умолчанию: **{dv}** (при создании стака)\n"
        f"\u200b**・** 🎙️ Максимум войсов: **{mv}** (предел улучшения)\n"
        f"\u200b**・** 💬 Текстовых по умолчанию: **{dt}**\n"
        f"\u200b**・** 💬 Максимум текстовых: **{mt}**\n\n"
        f"*Используйте кнопки ниже для изменения.*"
    )
    return embed


async def _change_param(inter, view, key, delta, min_v, max_v):
    data = await load_config()
    ss = data.setdefault("settings", {}).setdefault("stacks", {})
    current = ss.get(key, 0)
    new = max(min_v, min(max_v, current + delta))
    if new == current:
        return await inter.response.send_message(embed=err_embed(f"Уже {'минимум' if delta < 0 else 'максимум'}."), ephemeral=True)
    ss[key] = new
    await save_config(data, updated_by=inter.author.id)
    await inter.response.edit_message(embed=await build_params_embed(), view=view)


class ParamsView(disnake.ui.View):
    def __init__(self, parent):
        super().__init__(timeout=300)
        self.parent = parent

    async def interaction_check(self, inter):
        return await self.parent.interaction_check(inter)

    @disnake.ui.button(label="🎙 Войсов -", style=disnake.ButtonStyle.secondary, row=0)
    async def v_minus(self, button, inter):
        await _change_param(inter, self, "default_voice_count", -1, 1, 10)

    @disnake.ui.button(label="🎙 Войсов +", style=disnake.ButtonStyle.secondary, row=0)
    async def v_plus(self, button, inter):
        await _change_param(inter, self, "default_voice_count", +1, 1, 10)

    @disnake.ui.button(label="🎙 Макс. войсов -", style=disnake.ButtonStyle.secondary, row=1)
    async def mv_minus(self, button, inter):
        await _change_param(inter, self, "max_voice_channels", -1, 1, 20)

    @disnake.ui.button(label="🎙 Макс. войсов +", style=disnake.ButtonStyle.secondary, row=1)
    async def mv_plus(self, button, inter):
        await _change_param(inter, self, "max_voice_channels", +1, 1, 20)

    @disnake.ui.button(label="💬 Текст -", style=disnake.ButtonStyle.secondary, row=2)
    async def t_minus(self, button, inter):
        await _change_param(inter, self, "default_text_count", -1, 1, 5)

    @disnake.ui.button(label="💬 Текст +", style=disnake.ButtonStyle.secondary, row=2)
    async def t_plus(self, button, inter):
        await _change_param(inter, self, "default_text_count", +1, 1, 5)

    @disnake.ui.button(label="💬 Макс. текст -", style=disnake.ButtonStyle.secondary, row=3)
    async def mt_minus(self, button, inter):
        await _change_param(inter, self, "max_text_channels", -1, 1, 10)

    @disnake.ui.button(label="💬 Макс. текст +", style=disnake.ButtonStyle.secondary, row=3)
    async def mt_plus(self, button, inter):
        await _change_param(inter, self, "max_text_channels", +1, 1, 10)

    @disnake.ui.button(label="Назад", style=disnake.ButtonStyle.grey, emoji="↩️", row=4)
    async def back(self, button, inter):
        await self.parent.restore(inter)


async def build_fullview_embed():
    import json
    data = await load_config()
    txt = json.dumps(data, ensure_ascii=False, indent=2)
    if len(txt) > 4000:
        txt = txt[:3950] + "\n…(обрезано)"
    embed = disnake.Embed(title="—・Полный конфиг (JSON)", description=f"```json\n{txt}\n```", color=COLOR_BG)
    return embed


class FullviewView(disnake.ui.View):
    def __init__(self, parent):
        super().__init__(timeout=300)
        self.parent = parent

    async def interaction_check(self, inter):
        return await self.parent.interaction_check(inter)

    @disnake.ui.button(label="Назад", style=disnake.ButtonStyle.grey, emoji="↩️")
    async def back(self, button, inter):
        await self.parent.restore(inter)


class LogEventEditView(disnake.ui.View):
    def __init__(self, parent, category_key, event_key, event_name):
        super().__init__(timeout=300)
        self.parent = parent
        self.category_key = category_key
        self.event_key = event_key
        self.event_name = event_name

    async def interaction_check(self, inter):
        return await self.parent.interaction_check(inter)

    async def update_buttons(self):
        data = await load_config()
        is_on = data.get("logging", {}).get(self.event_key, {}).get("enabled", False)
        for child in self.children:
            if getattr(child, "custom_id", None) == "toggle_log":
                child.label = "Выключить" if is_on else "Включить"
                child.style = disnake.ButtonStyle.danger if is_on else disnake.ButtonStyle.success

    @disnake.ui.button(label="Переключить", style=disnake.ButtonStyle.primary, custom_id="toggle_log", row=0)
    async def toggle(self, button, inter):
        data = await load_config()
        data.setdefault("logging", {}).setdefault(self.event_key, {"enabled": False, "channel": 0})
        data["logging"][self.event_key]["enabled"] = not data["logging"][self.event_key].get("enabled", False)
        await save_config(data, updated_by=inter.author.id)
        await self.update_buttons()
        await inter.response.edit_message(embed=await get_log_event_embed(self.event_key, self.event_name), view=self)

    @disnake.ui.channel_select(placeholder="Индивидуальный канал", channel_types=[disnake.ChannelType.text], min_values=0, max_values=1, row=1)
    async def sel_ch(self, select, inter):
        data = await load_config()
        data.setdefault("logging", {}).setdefault(self.event_key, {"enabled": False, "channel": 0})
        data["logging"][self.event_key]["channel"] = select.values[0].id if select.values else 0
        await save_config(data, updated_by=inter.author.id)
        await inter.response.edit_message(embed=await get_log_event_embed(self.event_key, self.event_name), view=self)

    @disnake.ui.button(label="Назад", style=disnake.ButtonStyle.grey, emoji="↩️", row=2)
    async def back(self, button, inter):
        view = LogCategoryEventsView(self.parent, self.category_key)
        await inter.response.edit_message(embed=await get_log_system_embed(), view=view)


class LogCategoryEventsView(disnake.ui.View):
    def __init__(self, parent, category_key):
        super().__init__(timeout=300)
        self.parent = parent
        self.category_key = category_key
        events = LOG_CATEGORIES[category_key][2]
        opts = [disnake.SelectOption(label=v, value=k) for k, v in events.items()]
        self.select = disnake.ui.StringSelect(placeholder="Выберите событие", options=opts[:25], row=0)
        self.select.callback = self.select_cb
        self.add_item(self.select)

    async def interaction_check(self, inter):
        return await self.parent.interaction_check(inter)

    async def select_cb(self, inter):
        val = self.select.values[0]
        event_name = LOG_CATEGORIES[self.category_key][2][val]
        view = LogEventEditView(self.parent, self.category_key, val, event_name)
        await view.update_buttons()
        await inter.response.edit_message(embed=await get_log_event_embed(val, event_name), view=view)

    @disnake.ui.button(label="Назад", style=disnake.ButtonStyle.grey, row=1)
    async def back(self, button, inter):
        await inter.response.edit_message(embed=await get_log_system_embed(), view=LogsView(self.parent))


class LogsView(disnake.ui.View):
    def __init__(self, parent):
        super().__init__(timeout=300)
        self.parent = parent
        opts = [disnake.SelectOption(label=v[0], value=k, emoji=v[1]) for k, v in LOG_CATEGORIES.items()]
        self.select = disnake.ui.StringSelect(placeholder="Выберите категорию", options=opts, row=0)
        self.select.callback = self.select_cb
        self.add_item(self.select)

    async def interaction_check(self, inter):
        return await self.parent.interaction_check(inter)

    async def select_cb(self, inter):
        val = self.select.values[0]
        await inter.response.edit_message(embed=await get_log_system_embed(), view=LogCategoryEventsView(self.parent, val))

    @disnake.ui.channel_select(placeholder="Канал серверных логов (общий)", channel_types=[disnake.ChannelType.text], min_values=0, max_values=1, row=1)
    async def sel_main(self, select, inter):
        data = await load_config()
        data.setdefault("settings", {}).setdefault("moderation", {})["server_log_channel_id"] = select.values[0].id if select.values else 0
        await save_config(data, updated_by=inter.author.id)
        await inter.response.edit_message(embed=await get_log_system_embed(), view=self)

    @disnake.ui.button(label="Включить всё", style=disnake.ButtonStyle.success, row=2)
    async def en_all(self, button, inter):
        data = await load_config()
        data.setdefault("logging", {})
        for _, _, events in LOG_CATEGORIES.values():
            for k in events.keys():
                data["logging"].setdefault(k, {"channel": 0})
                data["logging"][k]["enabled"] = True
        await save_config(data, updated_by=inter.author.id)
        await inter.response.edit_message(embed=await get_log_system_embed(), view=self)

    @disnake.ui.button(label="Выключить всё", style=disnake.ButtonStyle.danger, row=2)
    async def dis_all(self, button, inter):
        data = await load_config()
        data.setdefault("logging", {})
        for _, _, events in LOG_CATEGORIES.values():
            for k in events.keys():
                data["logging"].setdefault(k, {"channel": 0})
                data["logging"][k]["enabled"] = False
        await save_config(data, updated_by=inter.author.id)
        await inter.response.edit_message(embed=await get_log_system_embed(), view=self)

    @disnake.ui.button(label="Назад", style=disnake.ButtonStyle.grey, emoji="↩️", row=2)
    async def back(self, button, inter):
        await self.parent.restore(inter)


async def build_vs_embed():
    data = await load_config()
    vs = data.get("settings", {}).get("voice_stats", {})
    counted = vs.get("counted_category_ids", [])
    cats_text = "\n".join(f"\u200b**・** <#{c}>" for c in counted) if counted else "\u200b**・** *все категории сервера*"
    afk = f"<#{vs.get('afk_channel_id')}>" if vs.get('afk_channel_id') else "*не задан*"
    return disnake.Embed(
        title="🎤  Voice Stats",
        description=(
            f"**Засчитываемые категории:**\n{cats_text}\n"
            f"*(пусто — считаются все категории кроме AFK)*\n\n"
            f"**AFK канал:** {afk}\n"
            f"*(время в этом канале не засчитывается)*"
        ),
        color=COLOR_BG
    )


class VoiceStatsView(disnake.ui.View):
    def __init__(self, parent):
        super().__init__(timeout=300)
        self.parent = parent

    async def interaction_check(self, inter):
        return inter.author.id == self.parent.author_id

    @disnake.ui.channel_select(
        placeholder="Засчитываемые категории (выбор) — пусто = все",
        channel_types=[disnake.ChannelType.category],
        min_values=0, max_values=25,
        row=0
    )
    async def sel_cats(self, select, inter):
        data = await load_config()
        ids = [c.id for c in select.values]
        data.setdefault("settings", {}).setdefault("voice_stats", {})["counted_category_ids"] = ids
        await save_config(data, inter.author.id)
        await inter.response.edit_message(embed=await build_vs_embed(), view=self)

    @disnake.ui.channel_select(
        placeholder="AFK канал (НЕ засчитывается)",
        channel_types=[disnake.ChannelType.voice],
        min_values=0, max_values=1,
        row=1
    )
    async def sel_afk(self, select, inter):
        data = await load_config()
        data.setdefault("settings", {}).setdefault("voice_stats", {})["afk_channel_id"] = (
            select.values[0].id if select.values else 0
        )
        await save_config(data, inter.author.id)
        await inter.response.edit_message(embed=await build_vs_embed(), view=self)

    @disnake.ui.button(label="Учитывать все категории", style=disnake.ButtonStyle.secondary, emoji="🌐", row=2)
    async def all_cats(self, button, inter):
        data = await load_config()
        data.setdefault("settings", {}).setdefault("voice_stats", {})["counted_category_ids"] = []
        await save_config(data, inter.author.id)
        await inter.response.edit_message(embed=await build_vs_embed(), view=self)

    @disnake.ui.button(label="Сбросить AFK", style=disnake.ButtonStyle.secondary, emoji="❌", row=2)
    async def clear_afk(self, button, inter):
        data = await load_config()
        data.setdefault("settings", {}).setdefault("voice_stats", {})["afk_channel_id"] = 0
        await save_config(data, inter.author.id)
        await inter.response.edit_message(embed=await build_vs_embed(), view=self)

    @disnake.ui.button(label="Назад", style=disnake.ButtonStyle.grey, emoji="↩️", row=2)
    async def back(self, button, inter):
        await self.parent.restore(inter)


async def build_vp_embed():
    data = await load_config()
    vp = data.get("settings", {}).get("voice_private", {})
    trigger = f"<#{vp.get('trigger_channel_id')}>" if vp.get('trigger_channel_id') else "*не задан*"
    cat = f"<#{vp.get('category_id')}>" if vp.get('category_id') else "*не задана*"
    return disnake.Embed(
        title="🔒  Приватные голосовые каналы",
        description=(
            f"**Триггер-канал:** {trigger}\n"
            f"*(голосовой канал — при заходе участнику создаётся личный)*\n\n"
            f"**Категория для приваток:** {cat}\n"
            f"*(где будут создаваться личные каналы)*\n\n"
            f"_Управление каналом — через кнопки прямо в канале (mute/ban/закрыть/лимит/название)._"
        ),
        color=COLOR_BG
    )


class VoicePrivateView(disnake.ui.View):
    def __init__(self, parent):
        super().__init__(timeout=300)
        self.parent = parent

    async def interaction_check(self, inter):
        return inter.author.id == self.parent.author_id

    @disnake.ui.channel_select(
        placeholder="Триггер-канал (войс)",
        channel_types=[disnake.ChannelType.voice],
        min_values=0, max_values=1,
        row=0
    )
    async def sel_trigger(self, select, inter):
        data = await load_config()
        data.setdefault("settings", {}).setdefault("voice_private", {})["trigger_channel_id"] = (
            select.values[0].id if select.values else 0
        )
        await save_config(data, inter.author.id)
        await inter.response.edit_message(embed=await build_vp_embed(), view=self)

    @disnake.ui.channel_select(
        placeholder="Категория для приватных каналов",
        channel_types=[disnake.ChannelType.category],
        min_values=0, max_values=1,
        row=1
    )
    async def sel_category(self, select, inter):
        data = await load_config()
        data.setdefault("settings", {}).setdefault("voice_private", {})["category_id"] = (
            select.values[0].id if select.values else 0
        )
        await save_config(data, inter.author.id)
        await inter.response.edit_message(embed=await build_vp_embed(), view=self)

    @disnake.ui.button(label="Назад", style=disnake.ButtonStyle.grey, emoji="↩️", row=2)
    async def back(self, button, inter):
        await self.parent.restore(inter)


async def build_ct_embed():
    data = await load_config()
    ct = data.get("settings", {}).get("clan_tag", {})
    role_id = ct.get("role_id", 0)
    role_text = f"<@&{role_id}>" if role_id else "*не задана*"
    enabled = ct.get("enabled", True)
    enabled_text = "🟢 включена" if enabled else "🔴 выключена"
    interval = ct.get("sync_interval_minutes", 15)

    return disnake.Embed(
        title="🏷️  Клан-тег",
        description=(
            f"**Авто-синхронизация:** {enabled_text}\n"
            f"**Роль для носящих тег:** {role_text}\n"
            f"**Интервал фоновой проверки:** **{interval}** мин\n\n"
            f"_Резервная синхронизация каждые N минут — на случай если Discord не пришлёт ивент. "
            f"Также проверяет тех кто в БД через REST: если кто-то снял тег — снимает роль."
            f"_\n\n"
            f"_В `/настройки → Логирование → Клан-тег` можно настроить лог-канал для событий._\n"
            f"_Для ручного запуска полного скана — `/сканировать_теги`._"
        ),
        color=COLOR_BG
    )


class ClanTagView(disnake.ui.View):
    def __init__(self, parent):
        super().__init__(timeout=300)
        self.parent = parent

    async def interaction_check(self, inter):
        return inter.author.id == self.parent.author_id

    @disnake.ui.role_select(
        placeholder="Роль выдаваемая носителям тега",
        min_values=0, max_values=1,
        row=0
    )
    async def sel_role(self, select, inter):
        data = await load_config()
        data.setdefault("settings", {}).setdefault("clan_tag", {})["role_id"] = (
            select.values[0].id if select.values else 0
        )
        await save_config(data, inter.author.id)
        await inter.response.edit_message(embed=await build_ct_embed(), view=self)

    @disnake.ui.button(label="Включить/выключить авто-синхр.", style=disnake.ButtonStyle.secondary, emoji="🔄", row=1)
    async def toggle_enabled(self, button, inter):
        data = await load_config()
        ct = data.setdefault("settings", {}).setdefault("clan_tag", {})
        ct["enabled"] = not ct.get("enabled", True)
        await save_config(data, inter.author.id)
        await inter.response.edit_message(embed=await build_ct_embed(), view=self)

    @disnake.ui.button(label="Интервал: -5 мин", style=disnake.ButtonStyle.secondary, row=1)
    async def interval_minus(self, button, inter):
        data = await load_config()
        ct = data.setdefault("settings", {}).setdefault("clan_tag", {})
        cur = int(ct.get("sync_interval_minutes", 15))
        ct["sync_interval_minutes"] = max(5, cur - 5)
        await save_config(data, inter.author.id)
        await inter.response.edit_message(embed=await build_ct_embed(), view=self)

    @disnake.ui.button(label="Интервал: +5 мин", style=disnake.ButtonStyle.secondary, row=1)
    async def interval_plus(self, button, inter):
        data = await load_config()
        ct = data.setdefault("settings", {}).setdefault("clan_tag", {})
        cur = int(ct.get("sync_interval_minutes", 15))
        ct["sync_interval_minutes"] = min(1440, cur + 5)
        await save_config(data, inter.author.id)
        await inter.response.edit_message(embed=await build_ct_embed(), view=self)

    @disnake.ui.button(label="Назад", style=disnake.ButtonStyle.grey, emoji="↩️", row=2)
    async def back(self, button, inter):
        await self.parent.restore(inter)


async def build_tc_embed():
    data = await load_config()
    tc = data.get("settings", {}).get("stacks", {}).get("tag_check", {})
    wl = tc.get("whitelist_user_ids", [])
    wl_text = "\n".join(f"\u200b**・** <@{u}>" for u in wl[:15]) if wl else "\u200b**・** *никого*"
    if len(wl) > 15:
        wl_text += f"\n*... и ещё {len(wl) - 15}*"
    notify = f"<#{tc.get('notify_channel_id')}>" if tc.get('notify_channel_id') else "*не задан*"
    grace = tc.get("grace_hours", 48)
    remind = tc.get("reminder_hours_before", 1)

    return disnake.Embed(
        title="🏷️  Проверка тега в стаках",
        description=(
            f"**Канал уведомлений:** {notify}\n"
            f"**Срок на постановку тега:** **{grace}** ч.\n"
            f"**DM-напоминание за:** **{remind}** ч. до кика\n\n"
            f"**Whitelist (могут без тега):**\n{wl_text}\n\n"
            f"_Менеджер/супер-админ запускает проверку через `/управление_стаком → "
            f"Проверить тег во всех стаках`. Бот тегает в канале уведомлений всех у кого нет "
            f"тега (по всем активным стакам), даёт срок. Через **{grace}** ч. кто не поставил — "
            f"автоматически кикается из стака. За **{remind}** ч. до кика — DM-напоминание._"
        ),
        color=COLOR_BG
    )


class TagCheckView(disnake.ui.View):
    def __init__(self, parent):
        super().__init__(timeout=300)
        self.parent = parent

    async def interaction_check(self, inter):
        return inter.author.id == self.parent.author_id

    @disnake.ui.user_select(
        placeholder="Whitelist (могут быть без тега) — до 25",
        min_values=0, max_values=25,
        row=0
    )
    async def sel_whitelist(self, select, inter):
        data = await load_config()
        ids = [u.id for u in select.values]
        data.setdefault("settings", {}).setdefault("stacks", {}).setdefault("tag_check", {})["whitelist_user_ids"] = ids
        await save_config(data, inter.author.id)
        await inter.response.edit_message(embed=await build_tc_embed(), view=self)

    @disnake.ui.channel_select(
        placeholder="Канал уведомлений",
        channel_types=[disnake.ChannelType.text],
        min_values=0, max_values=1,
        row=1
    )
    async def sel_channel(self, select, inter):
        data = await load_config()
        data.setdefault("settings", {}).setdefault("stacks", {}).setdefault("tag_check", {})["notify_channel_id"] = (
            select.values[0].id if select.values else 0
        )
        await save_config(data, inter.author.id)
        await inter.response.edit_message(embed=await build_tc_embed(), view=self)

    @disnake.ui.button(label="Срок: -6 ч.", style=disnake.ButtonStyle.secondary, row=2)
    async def grace_minus(self, button, inter):
        data = await load_config()
        tc = data.setdefault("settings", {}).setdefault("stacks", {}).setdefault("tag_check", {})
        cur = int(tc.get("grace_hours", 48))
        tc["grace_hours"] = max(1, cur - 6)
        await save_config(data, inter.author.id)
        await inter.response.edit_message(embed=await build_tc_embed(), view=self)

    @disnake.ui.button(label="Срок: +6 ч.", style=disnake.ButtonStyle.secondary, row=2)
    async def grace_plus(self, button, inter):
        data = await load_config()
        tc = data.setdefault("settings", {}).setdefault("stacks", {}).setdefault("tag_check", {})
        cur = int(tc.get("grace_hours", 48))
        tc["grace_hours"] = min(720, cur + 6)
        await save_config(data, inter.author.id)
        await inter.response.edit_message(embed=await build_tc_embed(), view=self)

    @disnake.ui.button(label="DM-напомин.: -1 ч.", style=disnake.ButtonStyle.secondary, row=2)
    async def remind_minus(self, button, inter):
        data = await load_config()
        tc = data.setdefault("settings", {}).setdefault("stacks", {}).setdefault("tag_check", {})
        cur = int(tc.get("reminder_hours_before", 1))
        tc["reminder_hours_before"] = max(0, cur - 1)
        await save_config(data, inter.author.id)
        await inter.response.edit_message(embed=await build_tc_embed(), view=self)

    @disnake.ui.button(label="DM-напомин.: +1 ч.", style=disnake.ButtonStyle.secondary, row=2)
    async def remind_plus(self, button, inter):
        data = await load_config()
        tc = data.setdefault("settings", {}).setdefault("stacks", {}).setdefault("tag_check", {})
        cur = int(tc.get("reminder_hours_before", 1))
        tc["reminder_hours_before"] = min(24, cur + 1)
        await save_config(data, inter.author.id)
        await inter.response.edit_message(embed=await build_tc_embed(), view=self)

    @disnake.ui.button(label="Назад", style=disnake.ButtonStyle.grey, emoji="↩️", row=3)
    async def back(self, button, inter):
        await self.parent.restore(inter)


class Settings(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def settings(self, inter):
        if not inter.response.is_done():
            await inter.response.defer(ephemeral=True, with_message=True)
        if not await is_staff(inter.author.id):
            return await inter.edit_original_response(embed=err_embed("Доступ запрещён."))
        embed = await build_main_embed(inter.guild)
        view = await build_main_view(inter.author.id)
        await inter.edit_original_response(embed=embed, view=view)


def setup(bot):
    bot.add_cog(Settings(bot))
