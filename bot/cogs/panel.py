import disnake
from disnake.ext import commands
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.config_manager import load_config

COLOR_DARK = 0x2b2d31
COLOR_GREEN = 0x9EE5B4
COLOR_RED = 0xF6C4C5
COLOR_ORANGE = 0xF8E3A1
COLOR_PURPLE = 0x9B59B6
COLOR_GOLD = 0xF1C40F


def _container(*children, color: int):
    c = disnake.ui.Container(*children)
    try:
        c.accent_colour = disnake.Colour(color)
    except:
        pass
    return c


async def _resolve_perm_level(user_id: int, member_roles: list) -> str:
    cfg = await load_config()
    s = cfg.settings
    if user_id == s.super_admin_id or user_id in s.manager_ids:
        return "super"
    if any(r in s.admin_role_ids for r in member_roles):
        return "admin"
    return "user"


def _is_admin(perm_level: str) -> bool:
    return perm_level in ("super", "admin")


async def _user_stack_role(bot, user_id: int):
    row = await bot.pool.fetchrow(
        "SELECT stack_id, stack_name, leader_id, TRUE AS is_leader FROM stacks WHERE leader_id = $1 AND status = 'active'",
        user_id
    )
    if row:
        return dict(row)
    row = await bot.pool.fetchrow(
        "SELECT s.stack_id, s.stack_name, s.leader_id, FALSE AS is_leader FROM stack_members sm JOIN stacks s ON s.stack_id = sm.stack_id WHERE sm.user_id = $1 AND s.status = 'active' LIMIT 1",
        user_id
    )
    return dict(row) if row else None


def _err_container(desc: str):
    return _container(
        disnake.ui.TextDisplay(f"# — · Ошибка\n\n{desc}"),
        color=COLOR_RED
    )


def _ok_container(title: str, desc: str):
    return _container(
        disnake.ui.TextDisplay(f"# — · {title}\n\n{desc}"),
        color=COLOR_GREEN
    )


def _info_container(title: str, desc: str):
    return _container(
        disnake.ui.TextDisplay(f"# — · {title}\n\n{desc}"),
        color=COLOR_DARK
    )


def _section(title: str, desc: str, btn_label: str, btn_id: str, btn_style=disnake.ButtonStyle.primary, emoji=None, disabled=False):
    return disnake.ui.Section(
        disnake.ui.TextDisplay(f"### {title}\n{desc}"),
        accessory=disnake.ui.Button(
            label=btn_label,
            style=btn_style,
            custom_id=btn_id,
            emoji=emoji,
            disabled=disabled
        )
    )


async def build_panel(bot, author_id: int, perm_level: str, user_stack: dict):
    sections = []
    is_admin = _is_admin(perm_level)

    if user_stack:
        if user_stack['is_leader']:
            sections.append(_section(
                "👑 Мой стак",
                f"Вы лидер **{user_stack['stack_name']}**. Управление участниками, каналами, набором.",
                "Открыть", "panel_my_stack_leader", disnake.ButtonStyle.primary, "👑"
            ))
        else:
            sections.append(_section(
                "📦 Мой стак",
                f"Вы в **{user_stack['stack_name']}**. Просмотр и выход.",
                "Открыть", "panel_my_stack_member", disnake.ButtonStyle.secondary, "📦"
            ))
    else:
        sections.append(_section(
            "📦 Нет стака",
            "Создайте свой стак или вступите в существующий.",
            "Создать", "panel_create_stack", disnake.ButtonStyle.success, "➕"
        ))

    if is_admin:
        sections.append(_section(
            "🛠️ Админ панель",
            "Управление стаками, архивы, панель заявок.",
            "Открыть", "panel_admin_any_stack", disnake.ButtonStyle.primary, "🛠️"
        ))
        sections.append(_section(
            "🔖 Проверка тегов",
            "ANTI тег у участников стаков. Кик через 48ч.",
            "Запустить", "panel_run_tag_check", disnake.ButtonStyle.secondary, "🔖"
        ))
        sections.append(_section(
            "🏷️ Клан-тег",
            "Сканирование, синхронизация ролей, список носителей.",
            "Открыть", "panel_clan_tag", disnake.ButtonStyle.secondary, "🏷️"
        ))
        sections.append(_section(
            "🎁 Розыгрыши",
            "Создать, управлять, реролл, статистика.",
            "Открыть", "panel_giveaways", disnake.ButtonStyle.secondary, "🎁"
        ))
        sections.append(_section(
            "👤 Профили и экономика",
            "Выдача валюты, опыта, репутации, ANTICOIN.",
            "Открыть", "panel_profiles", disnake.ButtonStyle.secondary, "👤"
        ))

    if perm_level == "super":
        sections.append(_section(
            "⚙️ Настройки",
            "Каналы, роли, лимиты, клан-тег, войс.",
            "Открыть", "panel_settings", disnake.ButtonStyle.success, "⚙️"
        ))

    status_parts = []
    if perm_level == "super":
        status_parts.append("🛡️ Супер-админ")
    elif is_admin:
        status_parts.append("🔧 Админ")
    else:
        status_parts.append("👤 Участник")
    if user_stack:
        status_parts.append(f"👑 Лидер {user_stack['stack_name']}" if user_stack['is_leader'] else f"📦 {user_stack['stack_name']}")

    header = disnake.ui.TextDisplay("# 🎛️ Панель управления\n\nДобро пожаловать. Выбери раздел ниже.")
    footer = disnake.ui.TextDisplay(f"-# {' • '.join(status_parts)}")

    children = [header, disnake.ui.Separator(divider=True)]
    for i, s in enumerate(sections):
        children.append(s)
        if i < len(sections) - 1:
            children.append(disnake.ui.Separator(divider=False))
    children.extend([disnake.ui.Separator(divider=True), footer])

    return [_container(*children, color=COLOR_DARK)]


class CreateStackModal(disnake.ui.Modal):
    def __init__(self, bot):
        self.bot = bot
        super().__init__(
            title="📦 Создание стака",
            components=[
                disnake.ui.TextInput(
                    label="Название",
                    placeholder="2-32 символа",
                    custom_id="name",
                    style=disnake.TextInputStyle.short,
                    max_length=32,
                    min_length=2
                )
            ]
        )

    async def callback(self, inter):
        from cogs.stacks import Stacks
        cog = self.bot.get_cog("Stacks")
        if not cog:
            return await inter.response.send_message("❌ Система стаков не загружена", ephemeral=True)
        await cog.create_stack.callback(cog, inter, название=inter.text_values["name"])


class ClanTagCheckUserView(disnake.ui.View):
    def __init__(self, author_id: int):
        super().__init__(timeout=180)
        self.author_id = author_id

    async def interaction_check(self, inter):
        return inter.author.id == self.author_id

    @disnake.ui.user_select(placeholder="Кого проверить?", min_values=1, max_values=1)
    async def pick(self, select, inter):
        member = select.values[0]
        cog = inter.bot.get_cog("ClanTag")
        if not cog:
            return await inter.response.send_message(components=[_err_container("Ког не загружен")], ephemeral=True)
        await cog.check_user_tag(inter, member)


def _clan_tag_menu():
    return _container(
        disnake.ui.TextDisplay("# 🏷️ Клан-тег\n\nОтслеживание тега гильдии."),
        disnake.ui.Separator(divider=True),
        _section("🔍 Сканирование", "Полный перебор участников, долго.", "Запустить", "panel_ct_scan", disnake.ButtonStyle.primary, "🔍"),
        disnake.ui.Separator(divider=False),
        _section("🛑 Отмена", "Остановить текущее сканирование.", "Отменить", "panel_ct_cancel", disnake.ButtonStyle.danger, "🛑"),
        disnake.ui.Separator(divider=False),
        _section("🔄 Синхронизация", "Выдать/снять роли по БД.", "Синхронизировать", "panel_ct_sync", disnake.ButtonStyle.primary, "🔄"),
        disnake.ui.Separator(divider=False),
        _section("📋 Список", "Кто носит тег сейчас.", "Открыть", "panel_ct_list", disnake.ButtonStyle.secondary, "📋"),
        disnake.ui.Separator(divider=False),
        _section("🩺 Диагностика", "Проверить конкретного юзера.", "Выбрать", "panel_ct_check_user", disnake.ButtonStyle.secondary, "🩺"),
        color=COLOR_DARK
    )


def _giveaway_menu():
    return _container(
        disnake.ui.TextDisplay("# 🎁 Розыгрыши\n\nУправление розыгрышами."),
        disnake.ui.Separator(divider=True),
        _section("🎁 Создать", "Выбор типа, длительности, приза.", "Создать", "panel_gw_create", disnake.ButtonStyle.primary, "🎁"),
        disnake.ui.Separator(divider=False),
        _section("🎛️ Управление", "Список, реролл, удаление.", "Открыть", "panel_gw_manage", disnake.ButtonStyle.secondary, "🎛️"),
        color=COLOR_DARK
    )


class EconomyPanel(disnake.ui.View):
    def __init__(self, bot, target_id: int, author_id: int):
        super().__init__(timeout=180)
        self.bot = bot
        self.target_id = target_id
        self.author_id = author_id

    async def interaction_check(self, inter):
        return inter.author.id == self.author_id

    @disnake.ui.button(label="💰 Монеты", style=disnake.ButtonStyle.success, emoji="💰", row=0)
    async def give_coins(self, btn, inter):
        await inter.response.send_modal(GiveCoinsModal(self.bot, self.target_id))

    @disnake.ui.button(label="💎 ANTICOIN", style=disnake.ButtonStyle.primary, emoji="💎", row=0)
    async def give_anticoin(self, btn, inter):
        await inter.response.send_modal(GiveAnticoinModal(self.bot, self.target_id))

    @disnake.ui.button(label="⭐ Опыт", style=disnake.ButtonStyle.secondary, emoji="⭐", row=1)
    async def give_exp(self, btn, inter):
        await inter.response.send_modal(GiveExpModal(self.bot, self.target_id))

    @disnake.ui.button(label="🏆 Репутация", style=disnake.ButtonStyle.secondary, emoji="🏆", row=1)
    async def give_rep(self, btn, inter):
        await inter.response.send_modal(GiveRepModal(self.bot, self.target_id))

    @disnake.ui.button(label="🔙 Назад", style=disnake.ButtonStyle.grey, emoji="🔙", row=2)
    async def back(self, btn, inter):
        await inter.response.edit_message(content="Меню закрыто.", view=None, embed=None)


class GiveCoinsModal(disnake.ui.Modal):
    def __init__(self, bot, target_id):
        self.bot = bot
        self.target_id = target_id
        super().__init__(
            title="💰 Выдача монет",
            components=[
                disnake.ui.TextInput(
                    label="Сумма",
                    placeholder="100",
                    custom_id="amount",
                    style=disnake.TextInputStyle.short,
                    max_length=10
                )
            ]
        )

    async def callback(self, inter):
        try:
            amount = int(inter.text_values["amount"])
            if amount <= 0:
                raise ValueError
        except:
            return await inter.response.send_message("❌ Неверная сумма.", ephemeral=True)

        await self.bot.pool.execute("""
            INSERT INTO user_profiles (user_id, balance, total_earned)
            VALUES ($1, $2, $2)
            ON CONFLICT (user_id) DO UPDATE
            SET balance = user_profiles.balance + $2,
                total_earned = user_profiles.total_earned + $2
        """, self.target_id, amount)

        await inter.response.send_message(
            f"✅ Выдано **{amount:,}** монет пользователю <@{self.target_id}>",
            ephemeral=True
        )


class GiveAnticoinModal(disnake.ui.Modal):
    def __init__(self, bot, target_id):
        self.bot = bot
        self.target_id = target_id
        super().__init__(
            title="💎 Выдача ANTICOIN",
            components=[
                disnake.ui.TextInput(
                    label="Количество",
                    placeholder="10",
                    custom_id="amount",
                    style=disnake.TextInputStyle.short,
                    max_length=10
                )
            ]
        )

    async def callback(self, inter):
        try:
            amount = int(inter.text_values["amount"])
            if amount <= 0:
                raise ValueError
        except:
            return await inter.response.send_message("❌ Неверное количество.", ephemeral=True)

        await self.bot.pool.execute("""
            INSERT INTO user_profiles (user_id, anticoin)
            VALUES ($1, $2)
            ON CONFLICT (user_id) DO UPDATE
            SET anticoin = user_profiles.anticoin + $2
        """, self.target_id, amount)

        await inter.response.send_message(
            f"✅ Выдано **{amount:,}** ANTICOIN пользователю <@{self.target_id}>",
            ephemeral=True
        )


class GiveExpModal(disnake.ui.Modal):
    def __init__(self, bot, target_id):
        self.bot = bot
        self.target_id = target_id
        super().__init__(
            title="⭐ Выдача опыта",
            components=[
                disnake.ui.TextInput(
                    label="Количество",
                    placeholder="100",
                    custom_id="amount",
                    style=disnake.TextInputStyle.short,
                    max_length=10
                )
            ]
        )

    async def callback(self, inter):
        try:
            amount = int(inter.text_values["amount"])
            if amount <= 0:
                raise ValueError
        except:
            return await inter.response.send_message("❌ Неверное количество.", ephemeral=True)

        profile = await self.bot.pool.fetchrow("SELECT level, exp FROM user_profiles WHERE user_id = $1", self.target_id)
        if not profile:
            await self.bot.pool.execute("INSERT INTO user_profiles (user_id, created_at) VALUES ($1, $2)", self.target_id, int(time.time()))
            profile = {"level": 1, "exp": 0}

        new_exp = profile["exp"] + amount
        level = profile["level"]
        level_up = False

        while new_exp >= level * 100:
            new_exp -= level * 100
            level += 1
            level_up = True

        await self.bot.pool.execute("UPDATE user_profiles SET exp = $2, level = $3 WHERE user_id = $1", self.target_id, new_exp, level)

        msg = f"✅ Выдано **{amount:,}** опыта пользователю <@{self.target_id}>"
        if level_up:
            msg += f"\n🎉 Новый уровень: **{level}**!"

        await inter.response.send_message(msg, ephemeral=True)


class GiveRepModal(disnake.ui.Modal):
    def __init__(self, bot, target_id):
        self.bot = bot
        self.target_id = target_id
        super().__init__(
            title="🏆 Выдача репутации",
            components=[
                disnake.ui.TextInput(
                    label="Количество",
                    placeholder="1",
                    custom_id="amount",
                    style=disnake.TextInputStyle.short,
                    max_length=5
                )
            ]
        )

    async def callback(self, inter):
        try:
            amount = int(inter.text_values["amount"])
            if amount <= 0:
                raise ValueError
        except:
            return await inter.response.send_message("❌ Неверное количество.", ephemeral=True)

        await self.bot.pool.execute("""
            INSERT INTO user_profiles (user_id, reputation)
            VALUES ($1, $2)
            ON CONFLICT (user_id) DO UPDATE
            SET reputation = user_profiles.reputation + $2
        """, self.target_id, amount)

        await inter.response.send_message(
            f"✅ Выдано **{amount}** репутации пользователю <@{self.target_id}>",
            ephemeral=True
        )


class Panel(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.slash_command(name="бот", description="Главная панель")
    async def panel(self, inter):
        await inter.response.defer(ephemeral=True, with_message=True)

        roles = [r.id for r in inter.author.roles] if hasattr(inter.author, 'roles') else []
        perm = await _resolve_perm_level(inter.author.id, roles)
        stack = await _user_stack_role(self.bot, inter.author.id)

        comp = await build_panel(self.bot, inter.author.id, perm, stack)
        await inter.edit_original_response(components=comp)

    @commands.Cog.listener()
    async def on_button_click(self, inter):
        cid = inter.component.custom_id or ""
        if not cid.startswith("panel_"):
            return

        roles = [r.id for r in inter.author.roles] if hasattr(inter.author, 'roles') else []
        perm = await _resolve_perm_level(inter.author.id, roles)
        stack = await _user_stack_role(self.bot, inter.author.id)

        handlers = {
            "panel_my_stack_leader": lambda: self._my_stack_leader(inter, stack),
            "panel_my_stack_member": lambda: self._my_stack_member(inter, stack),
            "panel_create_stack": lambda: self._create_stack(inter),
            "panel_admin_any_stack": lambda: self._admin_stack(inter, perm),
            "panel_run_tag_check": lambda: self._run_tag_check(inter, perm),
            "panel_clan_tag": lambda: self._clan_tag_menu(inter, perm),
            "panel_giveaways": lambda: self._giveaway_menu(inter, perm),
            "panel_profiles": lambda: self._profiles_menu(inter, perm),
            "panel_settings": lambda: self._settings(inter, perm),
            "panel_ct_scan": lambda: self._ct_scan(inter, perm),
            "panel_ct_cancel": lambda: self._ct_cancel(inter, perm),
            "panel_ct_sync": lambda: self._ct_sync(inter, perm),
            "panel_ct_list": lambda: self._ct_list(inter, perm),
            "panel_ct_check_user": lambda: self._ct_check(inter, perm),
            "panel_gw_create": lambda: self._gw_create(inter, perm),
            "panel_gw_manage": lambda: self._gw_manage(inter, perm),
        }

        handler = handlers.get(cid)
        if handler:
            await handler()

    async def _my_stack_leader(self, inter, stack):
        if not stack or not stack['is_leader']:
            return await inter.response.send_message(components=[_err_container("Вы не лидер")], ephemeral=True)
        from cogs.stacks import _build_profile_embed, ActiveStackActionsView, _user_permission_level
        roles = [r.id for r in inter.author.roles] if hasattr(inter.author, 'roles') else []
        perm = await _user_permission_level(inter.author.id, roles)
        embed = await _build_profile_embed(self.bot, stack['stack_id'])
        view = ActiveStackActionsView(self.bot, stack['stack_id'], inter.author.id, perm, is_owner=True)
        await inter.response.send_message(embed=embed, view=view, ephemeral=True)

    async def _my_stack_member(self, inter, stack):
        if not stack:
            return await inter.response.send_message(components=[_err_container("Вы не в стаке")], ephemeral=True)
        from cogs.stacks import _build_profile_embed, MemberProfileView
        embed = await _build_profile_embed(self.bot, stack['stack_id'])
        view = MemberProfileView(self.bot, stack['stack_id'], inter.author.id)
        await inter.response.send_message(embed=embed, view=view, ephemeral=True)

    async def _create_stack(self, inter):
        await inter.response.send_modal(CreateStackModal(self.bot))

    async def _admin_stack(self, inter, perm):
        if not _is_admin(perm):
            return await inter.response.send_message(components=[_err_container("Нет прав")], ephemeral=True)

        from cogs.stacks import StackOwnerChoiceView, AdminNoOwnChoiceView

        own = await self.bot.pool.fetchrow("SELECT stack_id FROM stacks WHERE leader_id = $1 AND status = 'active'", inter.author.id)

        class ExtraBtns(disnake.ui.View):
            def __init__(self, parent, bot, uid, lvl):
                super().__init__(timeout=180)
                self.parent = parent
                self.bot = bot
                self.uid = uid
                self.lvl = lvl
            async def interaction_check(self, i):
                return i.author.id == self.uid
            @disnake.ui.button(label="📂 Архив", style=disnake.ButtonStyle.secondary, emoji="📂", row=0)
            async def a(self, b, i): await self.parent._archive(i, self.lvl)
            @disnake.ui.button(label="📋 Панель заявок", style=disnake.ButtonStyle.secondary, emoji="📋", row=0)
            async def r(self, b, i): await self.parent._repost(i, self.lvl)
            @disnake.ui.button(label="🔖 Тег-чек", style=disnake.ButtonStyle.secondary, emoji="🔖", row=1)
            async def t(self, b, i): await self.parent._run_tag_check(i, self.lvl)
            @disnake.ui.button(label="✖️ Закрыть", style=disnake.ButtonStyle.grey, emoji="✖️", row=1)
            async def c(self, b, i): await i.response.edit_message(components=[_info_container("Закрыто", "")])

        if own:
            view = StackOwnerChoiceView(self.bot, inter.author.id, own['stack_id'], perm)
            for btn in ExtraBtns(self, self.bot, inter.author.id, perm).children:
                view.add_item(btn)
            embed = disnake.Embed(title="— · Управление стаками", description="У вас есть свой стак + права админа", color=COLOR_DARK)
            return await inter.response.send_message(embed=embed, view=view, ephemeral=True)

        rows = await self.bot.pool.fetch("SELECT stack_id, stack_name, leader_id FROM stacks WHERE guild_id = $1 AND status = 'active' ORDER BY stack_name", inter.guild.id)
        if not rows:
            embed = disnake.Embed(title="— · Управление стаками", description="Нет активных стаков", color=COLOR_DARK)
            return await inter.response.send_message(embed=embed, view=ExtraBtns(self, self.bot, inter.author.id, perm), ephemeral=True)

        view = AdminNoOwnChoiceView(self.bot, inter.author.id, rows, perm)
        for btn in ExtraBtns(self, self.bot, inter.author.id, perm).children:
            view.add_item(btn)
        embed = disnake.Embed(title="— · Управление стаками", description="Выбери стак", color=COLOR_DARK)
        await inter.response.send_message(embed=embed, view=view, ephemeral=True)

    async def _archive(self, inter, perm):
        if not _is_admin(perm):
            return await inter.response.send_message(components=[_err_container("Нет прав")], ephemeral=True)
        from cogs.stacks import ArchiveManagementView
        rows = await self.bot.pool.fetch("SELECT stack_id, stack_name, leader_id FROM stacks WHERE guild_id = $1 AND status = 'archived' ORDER BY archive_at DESC", inter.guild.id)
        if not rows:
            return await inter.response.send_message(components=[_info_container("Архив", "Нет архивных стаков")], ephemeral=True)
        view = ArchiveManagementView(self.bot, inter.author.id, rows, perm)
        await inter.response.send_message(embed=view._get_embed(), view=view, ephemeral=True)

    async def _repost(self, inter, perm):
        if not _is_admin(perm):
            return await inter.response.send_message(components=[_err_container("Нет прав")], ephemeral=True)
        await inter.response.defer(ephemeral=True, with_message=True)
        from cogs.stacks import refresh_applications_panel
        cfg = await load_config()
        ch_id = cfg.settings.stacks.applications_channel_id
        if not ch_id:
            return await inter.edit_original_response(components=[_err_container("Канал не настроен")])
        ch = inter.guild.get_channel(ch_id)
        if not ch:
            return await inter.edit_original_response(components=[_err_container("Канал не найден")])
        try:
            await refresh_applications_panel(self.bot, inter.guild)
            await inter.edit_original_response(components=[_container(disnake.ui.TextDisplay("# · Готово\n\nПанель переотправлена"), color=COLOR_GREEN)])
        except Exception as e:
            await inter.edit_original_response(components=[_err_container(f"Ошибка: {e}")])

    async def _run_tag_check(self, inter, perm):
        if not _is_admin(perm):
            return await inter.response.send_message(components=[_err_container("Нет прав")], ephemeral=True)
        await inter.response.defer(ephemeral=True, with_message=True)
        from cogs.stacks import _run_check_tags_all
        await _run_check_tags_all(self.bot, inter)

    async def _clan_tag_menu(self, inter, perm):
        if not _is_admin(perm):
            return await inter.response.send_message(components=[_err_container("Нет прав")], ephemeral=True)
        await inter.response.send_message(components=[_clan_tag_menu()], ephemeral=True)

    async def _giveaway_menu(self, inter, perm):
        if not _is_admin(perm):
            return await inter.response.send_message(components=[_err_container("Нет прав")], ephemeral=True)
        await inter.response.send_message(components=[_giveaway_menu()], ephemeral=True)

    async def _profiles_menu(self, inter, perm):
        if not _is_admin(perm):
            return await inter.response.send_message(components=[_err_container("Нет прав")], ephemeral=True)

        embed = disnake.Embed(
            title="👤 Экономика и профили",
            description="Выберите пользователя для выдачи ресурсов\n\n"
                        "**Монеты** — основная валюта\n"
                        "**ANTICOIN** — донат валюта\n"
                        "**Опыт** — повышает уровень\n"
                        "**Репутация** — влияние",
            color=COLOR_PURPLE
        )

        class UserSelectView(disnake.ui.View):
            def __init__(self, bot, author_id):
                super().__init__(timeout=180)
                self.bot = bot
                self.author_id = author_id

            async def interaction_check(self, i):
                return i.author.id == self.author_id

            @disnake.ui.user_select(placeholder="Выберите пользователя", min_values=1, max_values=1)
            async def select_user(self, select, i):
                target = select.values[0]
                await i.response.send_message(
                    f"✅ Выбран {target.mention}\n\nВыберите что выдать:",
                    view=EconomyPanel(self.bot, target.id, i.author.id),
                    ephemeral=True
                )

            @disnake.ui.button(label="🔙 Закрыть", style=disnake.ButtonStyle.grey, emoji="🔙", row=1)
            async def close(self, btn, i):
                await i.response.edit_message(content="Меню закрыто.", view=None, embed=None)

        view = UserSelectView(self.bot, inter.author.id)
        await inter.response.send_message(embed=embed, view=view, ephemeral=True)

    async def _settings(self, inter, perm):
        if perm != "super":
            return await inter.response.send_message(components=[_err_container("Только для главного")], ephemeral=True)
        cog = self.bot.get_cog("Settings")
        if not cog:
            return await inter.response.send_message(components=[_err_container("Ког не загружен")], ephemeral=True)
        await inter.response.defer(ephemeral=True, with_message=True)
        await cog.settings(inter)

    async def _ct_scan(self, inter, perm):
        if not _is_admin(perm):
            return await inter.response.send_message(components=[_err_container("Нет прав")], ephemeral=True)
        cog = self.bot.get_cog("ClanTag")
        if not cog:
            return await inter.response.send_message(components=[_err_container("Ког не загружен")], ephemeral=True)
        await inter.response.defer(ephemeral=True, with_message=True)
        await cog.full_scan(inter)

    async def _ct_cancel(self, inter, perm):
        if not _is_admin(perm):
            return await inter.response.send_message(components=[_err_container("Нет прав")], ephemeral=True)
        cog = self.bot.get_cog("ClanTag")
        if not cog:
            return await inter.response.send_message(components=[_err_container("Ког не загружен")], ephemeral=True)
        await inter.response.defer(ephemeral=True, with_message=True)
        await cog.cancel_scan(inter)

    async def _ct_sync(self, inter, perm):
        if not _is_admin(perm):
            return await inter.response.send_message(components=[_err_container("Нет прав")], ephemeral=True)
        cog = self.bot.get_cog("ClanTag")
        if not cog:
            return await inter.response.send_message(components=[_err_container("Ког не загружен")], ephemeral=True)
        await inter.response.defer(ephemeral=True, with_message=True)
        await cog.force_role_sync(inter)

    async def _ct_list(self, inter, perm):
        if not _is_admin(perm):
            return await inter.response.send_message(components=[_err_container("Нет прав")], ephemeral=True)
        cog = self.bot.get_cog("ClanTag")
        if not cog:
            return await inter.response.send_message(components=[_err_container("Ког не загружен")], ephemeral=True)
        await inter.response.defer(ephemeral=True, with_message=True)
        await cog.list_wearers(inter)

    async def _ct_check(self, inter, perm):
        if not _is_admin(perm):
            return await inter.response.send_message(components=[_err_container("Нет прав")], ephemeral=True)
        embed = disnake.Embed(title="🩺 Диагностика", description="Выбери пользователя", color=COLOR_DARK)
        await inter.response.send_message(embed=embed, view=ClanTagCheckUserView(inter.author.id), ephemeral=True)

    async def _gw_create(self, inter, perm):
        if not _is_admin(perm):
            return await inter.response.send_message(components=[_err_container("Нет прав")], ephemeral=True)
        cog = self.bot.get_cog("Giveaways")
        if not cog:
            return await inter.response.send_message(components=[_err_container("Ког не загружен")], ephemeral=True)
        await inter.response.defer(ephemeral=True, with_message=True)
        await cog.create_giveaway(inter)

    async def _gw_manage(self, inter, perm):
        if not _is_admin(perm):
            return await inter.response.send_message(components=[_err_container("Нет прав")], ephemeral=True)
        cog = self.bot.get_cog("Giveaways")
        if not cog:
            return await inter.response.send_message(components=[_err_container("Ког не загружен")], ephemeral=True)
        await inter.response.defer(ephemeral=True, with_message=True)
        await cog.manage_giveaways(inter)


def setup(bot):
    bot.add_cog(Panel(bot))