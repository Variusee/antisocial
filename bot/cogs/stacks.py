import disnake
from disnake.ext import commands
import time
import re
import sys

sys.path.insert(0, "/root/antisocial")
from shared.config_manager import load_config, save_config
from shared.config_models import RootConfig
from shared.staff import is_admin_user

STATUS_PENDING = "pending_approval"
STATUS_ACTIVE = "active"
STATUS_ARCHIVED = "archived"

NAME_REGEX = re.compile(r"^[\w\s\-\u2600-\u27BF\U0001F300-\U0001F9FF]{2,32}$", re.UNICODE)

COLOR_OK = 0x9EE5B4
COLOR_ERR = 0xF6C4C5
COLOR_WARN = 0xF8E3A1
COLOR_NEUTRAL = 0x2b2d31
COLOR_DANGER = 0xE74C3C


def err_embed(desc):
    return disnake.Embed(title="—・Ошибка", description=desc, color=COLOR_ERR)


def ok_embed(desc):
    return disnake.Embed(title="—・Успешно", description=desc, color=COLOR_OK)


def info_embed(title, desc=""):
    return disnake.Embed(title=f"—・{title}", description=desc, color=COLOR_NEUTRAL)


async def _user_permission_level(user_id: int, member_roles: list) -> str:
    cfg = await load_config()
    if user_id == cfg.settings.super_admin_id or user_id in cfg.settings.manager_ids:
        return "super"
    if any(r in cfg.settings.admin_role_ids for r in member_roles):
        return "admin"
    return "user"


def _is_privileged(perm_level: str) -> bool:
    return perm_level in ("super", "admin")


class ApprovalView(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @disnake.ui.button(label="Одобрить", style=disnake.ButtonStyle.success, custom_id="stack_approve", emoji="✅")
    async def approve(self, button, inter):
        pass

    @disnake.ui.button(label="Отклонить", style=disnake.ButtonStyle.danger, custom_id="stack_reject", emoji="❌")
    async def reject(self, button, inter):
        pass


PANEL_PAGE_SIZE = 25


class ApplicationsPanelView(disnake.ui.View):
    def __init__(self, stacks_list, page: int = 0):
        super().__init__(timeout=None)
        self.stacks_list = stacks_list
        self.page = page
        self._build()

    def _total_pages(self):
        if not self.stacks_list:
            return 1
        return (len(self.stacks_list) - 1) // PANEL_PAGE_SIZE + 1

    def _build(self):
        self.clear_items()
        total_pages = self._total_pages()
        if self.page >= total_pages:
            self.page = total_pages - 1
        if self.page < 0:
            self.page = 0
        start = self.page * PANEL_PAGE_SIZE
        end = start + PANEL_PAGE_SIZE
        page_stacks = self.stacks_list[start:end]

        if page_stacks:
            options = [
                disnake.SelectOption(
                    label=s['stack_name'][:80],
                    description=f"ID: #{s['stack_id']:04d}",
                    value=str(s['stack_id']),
                    emoji="📋"
                )
                for s in page_stacks
            ]
            sel = disnake.ui.StringSelect(
                placeholder=f"Выберите стак для подачи заявки (стр. {self.page+1}/{total_pages})",
                options=options,
                custom_id=f"stacks_apply_select_p{self.page}",
                row=0,
            )
            self.add_item(sel)

        if total_pages > 1:
            prev_b = disnake.ui.Button(
                label="◀",
                style=disnake.ButtonStyle.secondary,
                custom_id=f"stacks_apply_prev_p{self.page}",
                disabled=self.page == 0,
                row=1,
            )
            self.add_item(prev_b)

            page_b = disnake.ui.Button(
                label=f"Стр. {self.page+1}/{total_pages}",
                style=disnake.ButtonStyle.grey,
                custom_id=f"stacks_apply_pagebtn_p{self.page}",
                disabled=True,
                row=1,
            )
            self.add_item(page_b)

            next_b = disnake.ui.Button(
                label="▶",
                style=disnake.ButtonStyle.secondary,
                custom_id=f"stacks_apply_next_p{self.page}",
                disabled=self.page >= total_pages - 1,
                row=1,
            )
            self.add_item(next_b)


class LeaderReviewView(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @disnake.ui.button(label="Принять", style=disnake.ButtonStyle.success, custom_id="app_accept", emoji="✅")
    async def accept(self, button, inter):
        pass

    @disnake.ui.button(label="Отклонить", style=disnake.ButtonStyle.danger, custom_id="app_reject", emoji="❌")
    async def reject(self, button, inter):
        pass


async def _build_profile_embed(bot, stack_id, guild=None):
    pool = bot.pool
    stack = await pool.fetchrow(
        "SELECT stack_id, leader_id, stack_name, role_id, category_id, created_at, status, "
        "archive_at, recruitment_open "
        "FROM stacks WHERE stack_id = $1",
        stack_id
    )
    if not stack:
        return err_embed("Стак не найден.")

    members = await pool.fetch("SELECT user_id FROM stack_members WHERE stack_id = $1 ORDER BY joined_at", stack_id)
    channels = await pool.fetch(
        "SELECT channel_id, channel_type FROM stack_channels WHERE stack_id = $1 ORDER BY channel_type, position",
        stack_id
    )

    status_map = {
        STATUS_PENDING: ("⏳ Ожидает одобрения", COLOR_WARN),
        STATUS_ACTIVE: ("🟢 Активный", COLOR_NEUTRAL),
        STATUS_ARCHIVED: ("📦 В архиве", 0x9E9DEA),
    }
    status_label, embed_color = status_map.get(stack['status'], ("?", COLOR_NEUTRAL))

    embed = disnake.Embed(title=f"—・Профиль стака: {stack['stack_name']}", color=embed_color)
    embed.add_field(name="> Статус:", value=f"\u200b**・** {status_label}", inline=True)
    embed.add_field(name="> Лидер:", value=f"\u200b**・** <@{stack['leader_id']}>", inline=True)
    embed.add_field(name="> Участников:", value=f"\u200b**・** **{len(members)}**", inline=True)

    if stack['status'] == STATUS_ACTIVE:
        recruit_label = "🔓 Набор открыт" if stack['recruitment_open'] else "🔒 Набор закрыт"
        embed.add_field(name="> Набор:", value=f"\u200b**・** {recruit_label}", inline=True)

    if stack['role_id']:
        embed.add_field(name="> Роль:", value=f"\u200b**・** <@&{stack['role_id']}>", inline=True)

    if stack['status'] == STATUS_ARCHIVED and stack['archive_at']:
        embed.add_field(name="> Автоудаление:", value=f"\u200b**・** <t:{stack['archive_at']}:R>", inline=True)

    voice_chs = [c for c in channels if c['channel_type'] == 'voice']
    text_chs = [c for c in channels if c['channel_type'] == 'text']
    ch_lines = []
    if text_chs:
        ch_lines.append("**Текстовые:** " + ", ".join(f"<#{c['channel_id']}>" for c in text_chs))
    if voice_chs:
        ch_lines.append("**Голосовые:** " + ", ".join(f"<#{c['channel_id']}>" for c in voice_chs))
    if ch_lines:
        embed.add_field(name="> Каналы:", value="\n".join(ch_lines), inline=False)

    member_list = "\n".join(f"\u200b**・** <@{m['user_id']}>" for m in members[:15])
    if len(members) > 15:
        member_list += f"\n\u200b**・** *…и ещё {len(members) - 15}*"
    embed.add_field(name="> Список участников:", value=member_list or "Пусто", inline=False)

    embed.add_field(name="> Создан:", value=f"\u200b**・** <t:{stack['created_at']}:f>", inline=False)
    embed.set_footer(text=f"ID стака: #{stack_id:04d}")
    return embed


async def _archive_stack(bot, stack_id, guild, archived_by):
    cfg = await load_config()
    pool = bot.pool
    stack = await pool.fetchrow("SELECT role_id, category_id, stack_name FROM stacks WHERE stack_id = $1", stack_id)
    if not stack:
        raise RuntimeError("Стак не найден")

    archive_cat_id = cfg.settings.stacks.archive_category_id
    archive_cat = guild.get_channel(archive_cat_id) if archive_cat_id else None
    if not archive_cat:
        archive_cat = await guild.create_category(
            name="📦 Архив стаков",
            overwrites={guild.default_role: disnake.PermissionOverwrite(view_channel=False)},
            reason="Автосоздание категории архива"
        )
        try:
            data = cfg.model_dump()
            data["settings"]["stacks"]["archive_category_id"] = archive_cat.id
            await save_config(RootConfig(**data))
        except Exception:
            pass

    channels = await pool.fetch("SELECT channel_id FROM stack_channels WHERE stack_id = $1", stack_id)
    for ch_row in channels:
        ch = guild.get_channel(ch_row['channel_id'])
        if ch:
            try:
                new_name = ("arch-" + ch.name) if not ch.name.startswith("arch-") else ch.name
                await ch.edit(category=archive_cat, name=new_name[:100])
                await ch.set_permissions(guild.default_role, view_channel=False)
            except Exception:
                pass

    category = guild.get_channel(stack['category_id'])
    if category:
        try:
            await category.delete(reason="Стак заархивирован")
        except Exception:
            pass

    role = guild.get_role(stack['role_id'])
    if role:
        try:
            await role.delete(reason="Стак заархивирован")
        except Exception:
            pass

    archive_at = int(time.time()) + 30 * 86400
    await pool.execute(
        "UPDATE stacks SET status = 'archived', archive_at = $1, category_id = 0, role_id = 0 WHERE stack_id = $2",
        archive_at, stack_id
    )


async def _hard_delete_stack(bot, stack_id, guild):
    pool = bot.pool
    stack = await pool.fetchrow("SELECT role_id, category_id, stack_name, status FROM stacks WHERE stack_id = $1", stack_id)
    if not stack:
        raise RuntimeError("Стак не найден")

    channels = await pool.fetch("SELECT channel_id FROM stack_channels WHERE stack_id = $1", stack_id)
    for ch_row in channels:
        ch = guild.get_channel(ch_row['channel_id'])
        if ch:
            try:
                await ch.delete(reason="Стак безвозвратно удалён")
            except Exception:
                pass

    if stack['category_id']:
        cat = guild.get_channel(stack['category_id'])
        if cat:
            try:
                await cat.delete(reason="Стак безвозвратно удалён")
            except Exception:
                pass

    if stack['role_id']:
        role = guild.get_role(stack['role_id'])
        if role:
            try:
                await role.delete(reason="Стак безвозвратно удалён")
            except Exception:
                pass

    await pool.execute("DELETE FROM stacks WHERE stack_id = $1", stack_id)


async def _restore_stack(bot, stack_id, guild):
    cfg = await load_config()
    pool = bot.pool
    stack = await pool.fetchrow("SELECT leader_id, stack_name, status FROM stacks WHERE stack_id = $1", stack_id)
    if not stack:
        raise RuntimeError("Стак не найден")
    if stack['status'] != STATUS_ARCHIVED:
        raise RuntimeError(f"Стак не в архиве (статус: {stack['status']})")

    exists_leader = await pool.fetchrow(
        "SELECT stack_id FROM stacks WHERE leader_id = $1 AND status IN ('pending_approval', 'active') AND stack_id != $2",
        stack['leader_id'], stack_id
    )
    if exists_leader:
        raise RuntimeError(f"У лидера <@{stack['leader_id']}> уже есть активный стак (#{exists_leader['stack_id']:04d}).")

    role = await guild.create_role(name=stack['stack_name'], reason=f"Восстановление стака #{stack_id}")
    leader_member = guild.get_member(stack['leader_id'])
    if leader_member:
        try:
            await leader_member.add_roles(role, reason="Восстановление: выдача роли лидеру")
        except Exception:
            pass

    overwrites = {
        guild.default_role: disnake.PermissionOverwrite(view_channel=False, connect=False),
        role: disnake.PermissionOverwrite(view_channel=True, connect=True, send_messages=True),
        guild.me: disnake.PermissionOverwrite(view_channel=True, connect=True, manage_channels=True, manage_roles=True),
    }

    category = await guild.create_category(name=stack['stack_name'], overwrites=overwrites, reason=f"Восстановление #{stack_id}")

    archived_channels = await pool.fetch(
        "SELECT channel_id, channel_type FROM stack_channels WHERE stack_id = $1 ORDER BY channel_type, position",
        stack_id
    )
    restored_refs = []
    failed_channels = []
    for row in archived_channels:
        old_ch = guild.get_channel(row['channel_id'])
        if old_ch:
            try:
                new_name = old_ch.name.removeprefix("arch-")
                await old_ch.edit(category=category, name=new_name, overwrites=overwrites)
                restored_refs.append(old_ch.mention)
            except Exception:
                failed_channels.append(old_ch.name)
        else:
            failed_channels.append(f"channel_id={row['channel_id']}")

    if not restored_refs:
        text_ch = await guild.create_text_channel(name="chat", category=category, overwrites=overwrites)
        await pool.execute("DELETE FROM stack_channels WHERE stack_id = $1", stack_id)
        await pool.execute(
            "INSERT INTO stack_channels (stack_id, channel_id, channel_type, position) VALUES ($1, $2, 'text', 0)",
            stack_id, text_ch.id
        )
        voice_channels = []
        for i in range(1, cfg.settings.stacks.default_voice_count + 1):
            vc = await guild.create_voice_channel(name=f"Voice {i}", category=category, overwrites=overwrites)
            voice_channels.append(vc)
            await pool.execute(
                "INSERT INTO stack_channels (stack_id, channel_id, channel_type, position) VALUES ($1, $2, 'voice', $3)",
                stack_id, vc.id, i
            )
        restored_refs = [text_ch.mention] + [vc.mention for vc in voice_channels]

    await pool.execute(
        "UPDATE stacks SET status = 'active', role_id = $1, category_id = $2, archive_at = 0 WHERE stack_id = $3",
        role.id, category.id, stack_id
    )

    members = await pool.fetch("SELECT user_id FROM stack_members WHERE stack_id = $1", stack_id)
    for m in members:
        if m['user_id'] == stack['leader_id']:
            continue
        member_obj = guild.get_member(m['user_id'])
        if member_obj:
            try:
                await member_obj.add_roles(role, reason=f"Восстановление стака {stack['stack_name']}")
            except Exception:
                pass

    return {
        "role": role,
        "category": category,
        "restored_channels": restored_refs,
        "failed_channels": failed_channels,
        "members_count": len(members),
    }


PANEL_IMAGE_PATH = "/root/antisocial/img/1.png"


def _build_panel_embed(stacks_list):
    embed = disnake.Embed(
        title="—・Заявки в стаки",
        description=(
            "Выберите стак из меню ниже, чтобы подать заявку.\n"
            "Лидер стака получит уведомление в личные сообщения."
        ),
        color=COLOR_NEUTRAL
    )
    if stacks_list:
        lines = [f"\u200b**・** **{s['stack_name']}** (ID `{s['stack_id']:04d}`)" for s in stacks_list[:15]]
        suffix = ""
        if len(stacks_list) > 15:
            suffix = f"\n\u200b**・** *…и ещё {len(stacks_list) - 15}*"
        embed.add_field(name="> Доступные стаки:", value="\n".join(lines) + suffix, inline=False)
    else:
        embed.add_field(name="> Доступных стаков нет", value="Пока ни один стак не был одобрен.", inline=False)
    import os as _os
    if _os.path.exists(PANEL_IMAGE_PATH):
        embed.set_image(url="attachment://1.png")
    return embed


async def refresh_applications_panel(bot, guild):
    import os as _os
    cfg = await load_config()
    pool = bot.pool
    ch_id = cfg.settings.stacks.applications_channel_id
    if not ch_id:
        return
    ch = guild.get_channel(ch_id)
    if not ch:
        return

    stacks_list = await pool.fetch(
        "SELECT stack_id, stack_name FROM stacks WHERE guild_id = $1 AND status = 'active' "
        "AND recruitment_open = TRUE ORDER BY stack_name",
        guild.id
    )

    embed = _build_panel_embed(stacks_list)
    view = ApplicationsPanelView(stacks_list, page=0) if stacks_list else None

    existing = await pool.fetchrow("SELECT message_id FROM applications_panel WHERE guild_id = $1", guild.id)
    if existing:
        try:
            old_msg = await ch.fetch_message(existing['message_id'])
            await old_msg.delete()
        except Exception:
            pass

    try:
        send_kwargs = {"embed": embed, "view": view}
        if _os.path.exists(PANEL_IMAGE_PATH):
            send_kwargs["file"] = disnake.File(PANEL_IMAGE_PATH, filename="1.png")
        new_msg = await ch.send(**send_kwargs)
        await pool.execute(
            "INSERT INTO applications_panel (guild_id, channel_id, message_id, updated_at) VALUES ($1, $2, $3, $4) "
            "ON CONFLICT (guild_id) DO UPDATE SET channel_id = $2, message_id = $3, updated_at = $4",
            guild.id, ch.id, new_msg.id, int(time.time())
        )
    except Exception as e:
        print(f"[STACKS] Не удалось отправить панель заявок: {e}", flush=True)


class ActiveStackActionsView(disnake.ui.View):
    def __init__(self, bot, stack_id, invoker_id, perm_level, is_owner):
        super().__init__(timeout=300)
        self.bot = bot
        self.stack_id = stack_id
        self.invoker_id = invoker_id
        self.perm_level = perm_level
        self.is_owner = is_owner

        if is_owner:
            reviewers_btn = disnake.ui.Button(
                label="Проверяющие",
                style=disnake.ButtonStyle.secondary,
                emoji="👥",
                row=1,
                custom_id=f"asm_reviewers_{stack_id}"
            )
            reviewers_btn.callback = self._open_reviewers
            self.add_item(reviewers_btn)

            applications_btn = disnake.ui.Button(
                label="Заявки",
                style=disnake.ButtonStyle.secondary,
                emoji="📋",
                row=1,
                custom_id=f"asm_apps_{stack_id}"
            )
            applications_btn.callback = self._open_applications
            self.add_item(applications_btn)

            recruitment_btn = disnake.ui.Button(
                label="Набор",
                style=disnake.ButtonStyle.secondary,
                emoji="🎟️",
                row=1,
                custom_id=f"asm_recruit_{stack_id}"
            )
            recruitment_btn.callback = self._open_recruitment
            self.add_item(recruitment_btn)

        if not is_owner and _is_privileged(perm_level):
            dissolve_btn = disnake.ui.Button(
                label="Распустить стак (удалить навсегда)",
                style=disnake.ButtonStyle.danger,
                emoji="💥",
                row=2,
                custom_id=f"asm_dissolve_{stack_id}"
            )
            dissolve_btn.callback = self._dissolve
            self.add_item(dissolve_btn)

    async def interaction_check(self, inter):
        if inter.author.id != self.invoker_id:
            await inter.response.send_message(embed=err_embed("Эта панель не для вас."), ephemeral=True)
            return False
        return True

    @disnake.ui.button(label="Управление каналами", style=disnake.ButtonStyle.primary, emoji="⚙️", row=0)
    async def upgrade(self, button, inter):
        view = UpgradeSelectView(self.bot, self.stack_id, self.invoker_id, self.perm_level, self.is_owner)
        embed = info_embed(
            "Управление каналами стака",
            "**Добавить:** +1 голосовой / +1 текстовый\n"
            "**Удалить:** −1 голосовой / −1 текстовый (с выбором)\n\n"
            "Минимум — 1 голосовой и 1 текстовый канал."
        )
        await inter.response.edit_message(embed=embed, view=view)

    @disnake.ui.button(label="Кикнуть участника", style=disnake.ButtonStyle.secondary, emoji="👢", row=0)
    async def kick_member(self, button, inter):
        pool = self.bot.pool
        stack = await pool.fetchrow("SELECT leader_id FROM stacks WHERE stack_id = $1", self.stack_id)
        rows = await pool.fetch(
            "SELECT user_id FROM stack_members WHERE stack_id = $1 AND user_id != $2",
            self.stack_id, stack['leader_id']
        )
        if not rows:
            embed = err_embed("В стаке нет других участников кроме лидера.")
            view = BackToProfileView(self.bot, self.stack_id, self.invoker_id, self.perm_level, self.is_owner)
            return await inter.response.edit_message(embed=embed, view=view)

        member_ids = [r['user_id'] for r in rows]
        view = KickSelectView(self.bot, self.stack_id, self.invoker_id, member_ids, self.perm_level, self.is_owner, inter.guild)
        embed = info_embed("Кик участника", "Выберите участника для исключения:")
        await inter.response.edit_message(embed=embed, view=view)

    @disnake.ui.button(label="Архивировать", style=disnake.ButtonStyle.danger, emoji="📦", row=1)
    async def archive(self, button, inter):
        view = ConfirmArchiveView(self.bot, self.stack_id, self.invoker_id, self.perm_level, self.is_owner)
        embed = disnake.Embed(
            title="—・Архивация стака",
            description=(
                "Стак и все его каналы/роль будут перенесены в **архив на 30 дней**.\n"
                "По истечении этого срока они удалятся **автоматически**.\n\n"
                "Восстановить стак из архива можно через `/архив_стаков`.\n\n"
                "**Продолжить?**"
            ),
            color=COLOR_WARN
        )
        await inter.response.edit_message(embed=embed, view=view)

    @disnake.ui.button(label="Закрыть", style=disnake.ButtonStyle.grey, emoji="✖️", row=3)
    async def close(self, button, inter):
        embed = info_embed("Меню закрыто", "Панель управления стаком закрыта.")
        await inter.response.edit_message(embed=embed, view=None)

    async def _open_reviewers(self, inter):
        view = ReviewersManageView(self.bot, self.stack_id, self.invoker_id, self.perm_level, self.is_owner)
        embed = await view._build_embed()
        await inter.response.edit_message(embed=embed, view=view)

    async def _open_applications(self, inter):
        view = PendingApplicationsView(
            self.bot, self.stack_id, self.invoker_id, self.perm_level, self.is_owner
        )
        embed = await view.build_embed()
        await inter.response.edit_message(embed=embed, view=view)

    async def _open_recruitment(self, inter):
        view = RecruitmentToggleView(
            self.bot, self.stack_id, self.invoker_id, self.perm_level, self.is_owner
        )
        embed = await view._build_embed()
        await inter.response.edit_message(embed=embed, view=view)

    async def _dissolve(self, inter):
        view = ConfirmDissolveView(self.bot, self.stack_id, self.invoker_id, self.perm_level, self.is_owner)
        embed = disnake.Embed(
            title="—・💥 Роспуск стака",
            description=(
                "Вы собираетесь **распустить чужой стак**.\n"
                "Каналы, роль, записи в БД будут удалены **немедленно и навсегда**.\n"
                "Лидер стака получит уведомление.\n\n"
                "**Это действие нельзя отменить.**"
            ),
            color=COLOR_DANGER
        )
        await inter.response.edit_message(embed=embed, view=view)


async def _run_check_tags_all(bot, inter):
    cog = bot.get_cog("StackTagCheck")
    if not cog:
        return await inter.followup.send(embed=err_embed("Модуль проверки тегов не загружен."), ephemeral=True)

    from shared.config_manager import load_config
    cfg = await load_config()
    ts = cfg.settings.stacks.tag_check
    if not ts.notify_channel_id:
        return await inter.followup.send(
            embed=err_embed(
                "Не задан канал уведомлений для тег-чека.\n"
                "Настройте в `/настройки → 🏷️ Стаки: проверка тега`."
            ),
            ephemeral=True
        )

    stacks_rows = await bot.pool.fetch(
        "SELECT stack_id FROM stacks WHERE status = 'active' ORDER BY stack_id"
    )
    if not stacks_rows:
        return await inter.followup.send(
            embed=info_embed("—・Нет активных стаков", "Не нашлось ни одного активного стака для проверки."),
            ephemeral=True
        )

    progress_msg = await inter.followup.send(embed=disnake.Embed(
        title="🔄  Запуск проверки",
        description=f"Проверка тегов запущена для **{len(stacks_rows)}** стаков. Прогресс будет обновляться...",
        color=COLOR_NEUTRAL
    ), ephemeral=True)

    total_members = 0
    total_new = 0
    total_still = 0
    total_skipped = 0
    stacks_with_violators = 0
    errors = 0

    for i, sr in enumerate(stacks_rows, start=1):
        try:
            result = await cog.run_tag_check_for_stack(inter.guild, sr['stack_id'], inter.author.id)
            if "error" in result:
                errors += 1
                continue
            total_members += result["members_total"]
            total_new += result["new_warned"]
            total_still += result["still_warned"]
            total_skipped += result["skipped_whitelist"]
            if result["new_warned"] > 0 or result["still_warned"] > 0:
                stacks_with_violators += 1
        except Exception as e:
            errors += 1
            print(f"[stacks._run_check_tags_all] стак {sr['stack_id']}: {type(e).__name__}: {e}", flush=True)

        if i % 5 == 0 or i == len(stacks_rows):
            try:
                pct = round(i / len(stacks_rows) * 100)
                bar_filled = int(pct / 5)
                bar = "█" * bar_filled + "░" * (20 - bar_filled)
                await progress_msg.edit(embed=disnake.Embed(
                    title="🔄  Проверка тегов...",
                    description=(
                        f"`{bar}` **{pct}%**\n"
                        f"\u200b**・** Стаков обработано: **{i}** / **{len(stacks_rows)}**\n"
                        f"\u200b**・** Новых предупреждений: **{total_new}**"
                    ),
                    color=COLOR_NEUTRAL
                ))
            except Exception:
                pass

    if total_new == 0 and total_still == 0:
        text = (
            f"✅  Все стаки чистые — у всех есть тег.\n\n"
            f"\u200b**・** Стаков проверено: **{len(stacks_rows)}**\n"
            f"\u200b**・** Участников проверено: **{total_members}**\n"
            f"\u200b**・** В whitelist (пропущено): **{total_skipped}**"
        )
        color = COLOR_OK
    else:
        text = (
            f"📢 Проверка тегов завершена.\n\n"
            f"\u200b**・** Стаков проверено: **{len(stacks_rows)}**\n"
            f"\u200b**・** Стаков с нарушителями: **{stacks_with_violators}**\n"
            f"\u200b**・** Участников проверено: **{total_members}**\n"
            f"\u200b**・** Новых предупреждений: **{total_new}**\n"
            f"\u200b**・** Уже были: **{total_still}**\n"
            f"\u200b**・** В whitelist: **{total_skipped}**\n"
            + (f"\u200b**・** Ошибок: **{errors}**\n" if errors else "")
            + f"\n_Уведомления отправлены в <#{ts.notify_channel_id}>._\n"
            f"_Срок постановки тега: **{ts.grace_hours}** ч._"
        )
        color = COLOR_WARN

    await progress_msg.edit(embed=disnake.Embed(
        title="—・Проверка тега во всех стаках",
        description=text,
        color=color
    ))


class ReviewersManageView(disnake.ui.View):
    def __init__(self, bot, stack_id, invoker_id, perm_level, is_owner):
        super().__init__(timeout=300)
        self.bot = bot
        self.stack_id = stack_id
        self.invoker_id = invoker_id
        self.perm_level = perm_level
        self.is_owner = is_owner

    async def interaction_check(self, inter):
        return inter.author.id == self.invoker_id

    async def _build_embed(self):
        pool = self.bot.pool
        stack = await pool.fetchrow("SELECT stack_name, leader_id FROM stacks WHERE stack_id = $1", self.stack_id)
        rows = await pool.fetch(
            "SELECT user_id, added_at FROM stack_reviewers WHERE stack_id = $1 ORDER BY added_at",
            self.stack_id
        )
        embed = disnake.Embed(
            title="—・Проверяющие стака",
            description=(
                f"**Стак:** {stack['stack_name'] if stack else '?'}\n"
                f"**Лидер:** <@{stack['leader_id']}> (всегда обрабатывает заявки)\n"
                f"**Проверяющих:** {len(rows)}\n\n"
                "Проверяющие получают копию заявки в ЛС и могут принимать/отклонять её "
                "наравне с лидером. Это могут быть только участники этого стака."
            ),
            color=COLOR_NEUTRAL
        )
        if rows:
            lines = "\n".join(
                f"\u200b**・** <@{r['user_id']}> (добавлен <t:{r['added_at']}:R>)" for r in rows[:15]
            )
            if len(rows) > 15:
                lines += f"\n\u200b**・** *…и ещё {len(rows) - 15}*"
            embed.add_field(name="> Текущие проверяющие:", value=lines, inline=False)
        else:
            embed.add_field(name="> Текущие проверяющие:", value="\u200b**・** *пока нет*", inline=False)
        return embed

    @disnake.ui.button(label="Добавить", style=disnake.ButtonStyle.success, emoji="➕", row=0)
    async def add(self, button, inter):
        pool = self.bot.pool
        stack = await pool.fetchrow("SELECT leader_id FROM stacks WHERE stack_id = $1", self.stack_id)
        candidates = await pool.fetch(
            """
            SELECT m.user_id FROM stack_members m
            WHERE m.stack_id = $1 AND m.user_id != $2
              AND NOT EXISTS (
                  SELECT 1 FROM stack_reviewers r WHERE r.stack_id = $1 AND r.user_id = m.user_id
              )
            ORDER BY m.joined_at
            """,
            self.stack_id, stack['leader_id']
        )
        if not candidates:
            embed = err_embed(
                "Нет подходящих кандидатов.\n"
                "Все участники стака уже проверяющие, или в стаке только лидер."
            )
            view = _ReviewersBackOnlyView(self)
            return await inter.response.edit_message(embed=embed, view=view)

        member_ids = [r['user_id'] for r in candidates]
        view = AddReviewerView(self.bot, self.stack_id, self.invoker_id, self.perm_level, self.is_owner,
                                member_ids, inter.guild, parent=self)
        embed = info_embed(
            "Добавить проверяющего",
            "Выберите участника стака, который сможет обрабатывать заявки наравне с лидером:"
        )
        await inter.response.edit_message(embed=embed, view=view)

    @disnake.ui.button(label="Удалить", style=disnake.ButtonStyle.danger, emoji="➖", row=0)
    async def remove(self, button, inter):
        pool = self.bot.pool
        rows = await pool.fetch(
            "SELECT user_id FROM stack_reviewers WHERE stack_id = $1 ORDER BY added_at",
            self.stack_id
        )
        if not rows:
            embed = err_embed("В стаке нет ни одного проверяющего.")
            view = _ReviewersBackOnlyView(self)
            return await inter.response.edit_message(embed=embed, view=view)
        items = []
        for r in rows:
            m = inter.guild.get_member(r['user_id']) if inter.guild else None
            label = m.display_name if m else f"Пользователь {r['user_id']}"
            items.append({"user_id": r['user_id'], "label": label})
        view = RemoveReviewerView(
            self.bot, self.stack_id, self.invoker_id, self.perm_level, self.is_owner,
            items=items, parent=self
        )
        embed = view._build_embed()
        await inter.response.edit_message(embed=embed, view=view)

    @disnake.ui.button(label="Назад", style=disnake.ButtonStyle.grey, emoji="↩️", row=1)
    async def back(self, button, inter):
        parent_view = ActiveStackActionsView(self.bot, self.stack_id, self.invoker_id, self.perm_level, self.is_owner)
        embed = await _build_profile_embed(self.bot, self.stack_id)
        await inter.response.edit_message(embed=embed, view=parent_view)


class _ReviewersBackOnlyView(disnake.ui.View):
    def __init__(self, parent: 'ReviewersManageView'):
        super().__init__(timeout=180)
        self.parent = parent

    async def interaction_check(self, inter):
        return inter.author.id == self.parent.invoker_id

    @disnake.ui.button(label="Назад", style=disnake.ButtonStyle.grey, emoji="↩️")
    async def back(self, button, inter):
        embed = await self.parent._build_embed()
        await inter.response.edit_message(embed=embed, view=self.parent)


class AddReviewerView(disnake.ui.View):
    PER_PAGE = 25

    def __init__(self, bot, stack_id, invoker_id, perm_level, is_owner, member_ids: list, guild, parent, page: int = 0):
        super().__init__(timeout=180)
        self.bot = bot
        self.stack_id = stack_id
        self.invoker_id = invoker_id
        self.perm_level = perm_level
        self.is_owner = is_owner
        self.guild = guild
        self.parent = parent
        self.all_items = []
        for uid in member_ids:
            m = guild.get_member(uid) if guild else None
            label = m.display_name if m else f"Пользователь {uid}"
            self.all_items.append({"user_id": uid, "label": label})
        self.page = page
        self._rebuild()

    async def interaction_check(self, inter):
        return inter.author.id == self.invoker_id

    def _total_pages(self):
        if not self.all_items:
            return 1
        return (len(self.all_items) - 1) // self.PER_PAGE + 1

    def _rebuild(self):
        self.clear_items()
        total_pages = self._total_pages()
        if self.page >= total_pages:
            self.page = total_pages - 1
        if self.page < 0:
            self.page = 0
        start = self.page * self.PER_PAGE
        end = start + self.PER_PAGE
        page_items = self.all_items[start:end]

        if page_items:
            options = [
                disnake.SelectOption(label=item['label'][:80], description=f"ID: {item['user_id']}", value=str(item['user_id']))
                for item in page_items
            ]
            sel = disnake.ui.StringSelect(
                placeholder=f"Выбрать (стр. {self.page+1}/{total_pages})",
                options=options, row=0
            )
            sel.callback = self._on_select
            self.add_item(sel)

        if total_pages > 1:
            prev_b = disnake.ui.Button(label="◀", style=disnake.ButtonStyle.secondary, row=1, disabled=self.page == 0)
            prev_b.callback = self._prev
            self.add_item(prev_b)
            page_b = disnake.ui.Button(label=f"Стр. {self.page+1}/{total_pages}", style=disnake.ButtonStyle.grey, disabled=True, row=1)
            self.add_item(page_b)
            next_b = disnake.ui.Button(label="▶", style=disnake.ButtonStyle.secondary, row=1, disabled=self.page >= total_pages - 1)
            next_b.callback = self._next
            self.add_item(next_b)

        back_b = disnake.ui.Button(label="Назад", style=disnake.ButtonStyle.grey, emoji="↩️", row=2)
        back_b.callback = self._back
        self.add_item(back_b)

    async def _on_select(self, inter):
        uid = int(inter.data.values[0])
        pool = self.bot.pool

        stack = await pool.fetchrow("SELECT leader_id FROM stacks WHERE stack_id = $1", self.stack_id)
        if not stack:
            return await inter.response.edit_message(embed=err_embed("Стак не найден."), view=self.parent)
        if uid == stack['leader_id']:
            return await inter.response.send_message(embed=err_embed("Лидер не может быть проверяющим."), ephemeral=True)
        is_member = await pool.fetchval(
            "SELECT 1 FROM stack_members WHERE stack_id = $1 AND user_id = $2",
            self.stack_id, uid
        )
        if not is_member:
            return await inter.response.send_message(embed=err_embed("Пользователь больше не участник стака."), ephemeral=True)

        elsewhere = await pool.fetchval(
            "SELECT stack_id FROM stack_reviewers WHERE user_id = $1",
            uid
        )
        if elsewhere:
            return await inter.response.send_message(
                embed=err_embed(f"Пользователь уже проверяющий в другом стаке (#{elsewhere:04d})."),
                ephemeral=True
            )

        await pool.execute(
            "INSERT INTO stack_reviewers (stack_id, user_id, added_at, added_by) VALUES ($1, $2, $3, $4) "
            "ON CONFLICT (stack_id, user_id) DO NOTHING",
            self.stack_id, uid, int(time.time()), inter.author.id
        )

        try:
            user = inter.guild.get_member(uid) or await self.bot.fetch_user(uid)
            stack_full = await pool.fetchrow("SELECT stack_name FROM stacks WHERE stack_id = $1", self.stack_id)
            await user.send(embed=ok_embed(
                f"Вы назначены **проверяющим** в стаке **{stack_full['stack_name']}**.\n"
                "Теперь вам будут приходить копии заявок в стак — вы сможете принимать или отклонять их."
            ))
        except Exception:
            pass

        embed = await self.parent._build_embed()
        await inter.response.edit_message(embed=embed, view=self.parent)

    async def _prev(self, inter):
        self.page -= 1
        self._rebuild()
        embed = info_embed("Добавить проверяющего", "Выберите участника стака:")
        await inter.response.edit_message(embed=embed, view=self)

    async def _next(self, inter):
        self.page += 1
        self._rebuild()
        embed = info_embed("Добавить проверяющего", "Выберите участника стака:")
        await inter.response.edit_message(embed=embed, view=self)

    async def _back(self, inter):
        embed = await self.parent._build_embed()
        await inter.response.edit_message(embed=embed, view=self.parent)


class RemoveReviewerView(disnake.ui.View):
    PER_PAGE = 25

    def __init__(self, bot, stack_id, invoker_id, perm_level, is_owner, items: list, parent, page: int = 0):
        super().__init__(timeout=180)
        self.bot = bot
        self.stack_id = stack_id
        self.invoker_id = invoker_id
        self.perm_level = perm_level
        self.is_owner = is_owner
        self.all_items = items
        self.parent = parent
        self.page = page
        self._rebuild()

    async def interaction_check(self, inter):
        return inter.author.id == self.invoker_id

    def _total_pages(self):
        if not self.all_items:
            return 1
        return (len(self.all_items) - 1) // self.PER_PAGE + 1

    def _build_embed(self):
        return info_embed(
            f"Удалить проверяющего • Стр. {self.page+1}/{self._total_pages()}",
            f"Всего проверяющих: **{len(self.all_items)}**\n\nВыберите кого удалить:"
        )

    def _rebuild(self):
        self.clear_items()
        total_pages = self._total_pages()
        if self.page >= total_pages:
            self.page = total_pages - 1
        if self.page < 0:
            self.page = 0
        start = self.page * self.PER_PAGE
        end = start + self.PER_PAGE
        page_items = self.all_items[start:end]

        if page_items:
            options = [
                disnake.SelectOption(label=item['label'][:80], description=f"ID: {item['user_id']}", value=str(item['user_id']))
                for item in page_items
            ]
            sel = disnake.ui.StringSelect(placeholder=f"Удалить (стр. {self.page+1}/{total_pages})", options=options, row=0)
            sel.callback = self._on_select
            self.add_item(sel)

        if total_pages > 1:
            prev_b = disnake.ui.Button(label="◀", style=disnake.ButtonStyle.secondary, row=1, disabled=self.page == 0)
            prev_b.callback = self._prev
            self.add_item(prev_b)
            page_b = disnake.ui.Button(label=f"Стр. {self.page+1}/{total_pages}", style=disnake.ButtonStyle.grey, disabled=True, row=1)
            self.add_item(page_b)
            next_b = disnake.ui.Button(label="▶", style=disnake.ButtonStyle.secondary, row=1, disabled=self.page >= total_pages - 1)
            next_b.callback = self._next
            self.add_item(next_b)

        back_b = disnake.ui.Button(label="Назад", style=disnake.ButtonStyle.grey, emoji="↩️", row=2)
        back_b.callback = self._back
        self.add_item(back_b)

    async def _on_select(self, inter):
        uid = int(inter.data.values[0])
        pool = self.bot.pool
        await pool.execute(
            "DELETE FROM stack_reviewers WHERE stack_id = $1 AND user_id = $2",
            self.stack_id, uid
        )
        try:
            stack_full = await pool.fetchrow("SELECT stack_name FROM stacks WHERE stack_id = $1", self.stack_id)
            user = inter.guild.get_member(uid) or await self.bot.fetch_user(uid)
            await user.send(embed=err_embed(
                f"С вас сняты полномочия **проверяющего** в стаке **{stack_full['stack_name']}**."
            ))
        except Exception:
            pass

        embed = await self.parent._build_embed()
        await inter.response.edit_message(embed=embed, view=self.parent)

    async def _prev(self, inter):
        self.page -= 1
        self._rebuild()
        await inter.response.edit_message(embed=self._build_embed(), view=self)

    async def _next(self, inter):
        self.page += 1
        self._rebuild()
        await inter.response.edit_message(embed=self._build_embed(), view=self)

    async def _back(self, inter):
        embed = await self.parent._build_embed()
        await inter.response.edit_message(embed=embed, view=self.parent)


class BackToProfileView(disnake.ui.View):
    def __init__(self, bot, stack_id, invoker_id, perm_level, is_owner):
        super().__init__(timeout=300)
        self.bot = bot
        self.stack_id = stack_id
        self.invoker_id = invoker_id
        self.perm_level = perm_level
        self.is_owner = is_owner

    async def interaction_check(self, inter):
        return inter.author.id == self.invoker_id

    @disnake.ui.button(label="Назад", style=disnake.ButtonStyle.grey, emoji="↩️")
    async def back(self, button, inter):
        parent_view = ActiveStackActionsView(self.bot, self.stack_id, self.invoker_id, self.perm_level, self.is_owner)
        embed = await _build_profile_embed(self.bot, self.stack_id)
        await inter.response.edit_message(embed=embed, view=parent_view)


class UpgradeSelectView(disnake.ui.View):
    def __init__(self, bot, stack_id, invoker_id, perm_level, is_owner):
        super().__init__(timeout=180)
        self.bot = bot
        self.stack_id = stack_id
        self.invoker_id = invoker_id
        self.perm_level = perm_level
        self.is_owner = is_owner

    async def interaction_check(self, inter):
        return inter.author.id == self.invoker_id

    @disnake.ui.button(label="+1 голосовой", style=disnake.ButtonStyle.primary, emoji="🔊", row=0)
    async def add_voice(self, button, inter):
        await self._do_upgrade(inter, "voice")

    @disnake.ui.button(label="+1 текстовый", style=disnake.ButtonStyle.primary, emoji="💬", row=0)
    async def add_text(self, button, inter):
        await self._do_upgrade(inter, "text")

    @disnake.ui.button(label="−1 голосовой", style=disnake.ButtonStyle.danger, emoji="🔊", row=1)
    async def remove_voice(self, button, inter):
        await self._open_delete_picker(inter, "voice")

    @disnake.ui.button(label="−1 текстовый", style=disnake.ButtonStyle.danger, emoji="💬", row=1)
    async def remove_text(self, button, inter):
        await self._open_delete_picker(inter, "text")

    @disnake.ui.button(label="Назад", style=disnake.ButtonStyle.grey, emoji="↩️", row=2)
    async def back(self, button, inter):
        parent_view = ActiveStackActionsView(self.bot, self.stack_id, self.invoker_id, self.perm_level, self.is_owner)
        embed = await _build_profile_embed(self.bot, self.stack_id)
        await inter.response.edit_message(embed=embed, view=parent_view)

    async def _open_delete_picker(self, inter, ch_type: str):
        pool = self.bot.pool
        rows = await pool.fetch(
            "SELECT channel_id, position FROM stack_channels WHERE stack_id = $1 AND channel_type = $2 ORDER BY position",
            self.stack_id, ch_type
        )
        if len(rows) <= 1:
            type_name = "голосовой" if ch_type == "voice" else "текстовый"
            embed = err_embed(f"Нельзя удалить последний {type_name} канал. Минимум — 1 канал каждого типа.")
            return await inter.response.edit_message(embed=embed, view=self)

        items = []
        for r in rows:
            ch = inter.guild.get_channel(r['channel_id'])
            if ch:
                items.append({"channel_id": ch.id, "name": ch.name, "mention": ch.mention})
            else:
                items.append({"channel_id": r['channel_id'], "name": f"канал {r['channel_id']}", "mention": f"<#{r['channel_id']}>"})

        view = ChannelDeletePickerView(
            self.bot, self.stack_id, self.invoker_id, self.perm_level, self.is_owner,
            ch_type=ch_type, items=items, parent=self
        )
        embed = view._build_embed()
        await inter.response.edit_message(embed=embed, view=view)

    async def _do_upgrade(self, inter, ch_type):
        await inter.response.defer()

        cfg = await load_config()
        pool = self.bot.pool
        max_voice = cfg.settings.stacks.max_voice_channels
        max_text = cfg.settings.stacks.max_text_channels

        current = await pool.fetchrow(
            "SELECT COUNT(*) FILTER (WHERE channel_type = 'voice') AS voices, "
            "COUNT(*) FILTER (WHERE channel_type = 'text') AS texts "
            "FROM stack_channels WHERE stack_id = $1",
            self.stack_id
        )
        voices = current['voices'] or 0
        texts = current['texts'] or 0

        if ch_type == "voice" and voices >= max_voice:
            embed = err_embed(f"Достигнут максимум голосовых каналов: **{max_voice}**.")
            view = BackToProfileView(self.bot, self.stack_id, self.invoker_id, self.perm_level, self.is_owner)
            return await inter.edit_original_message(embed=embed, view=view)
        if ch_type == "text" and texts >= max_text:
            embed = err_embed(f"Достигнут максимум текстовых каналов: **{max_text}**.")
            view = BackToProfileView(self.bot, self.stack_id, self.invoker_id, self.perm_level, self.is_owner)
            return await inter.edit_original_message(embed=embed, view=view)

        stack = await pool.fetchrow("SELECT role_id, category_id, stack_name FROM stacks WHERE stack_id = $1", self.stack_id)
        if not stack:
            embed = err_embed("Стак не найден.")
            view = BackToProfileView(self.bot, self.stack_id, self.invoker_id, self.perm_level, self.is_owner)
            return await inter.edit_original_message(embed=embed, view=view)

        guild = inter.guild
        category = guild.get_channel(stack['category_id'])
        role = guild.get_role(stack['role_id'])
        if not category or not role:
            embed = err_embed("Каналы или роль стака не найдены на сервере.")
            view = BackToProfileView(self.bot, self.stack_id, self.invoker_id, self.perm_level, self.is_owner)
            return await inter.edit_original_message(embed=embed, view=view)

        overwrites = {
            guild.default_role: disnake.PermissionOverwrite(view_channel=False, connect=False),
            role: disnake.PermissionOverwrite(view_channel=True, connect=True, send_messages=True),
            guild.me: disnake.PermissionOverwrite(view_channel=True, connect=True, manage_channels=True),
        }

        try:
            if ch_type == "voice":
                new_num = voices + 1
                ch = await guild.create_voice_channel(name=f"Voice {new_num}", category=category, overwrites=overwrites)
                position = new_num
            else:
                new_num = texts + 1
                suffix = f"-{new_num}" if new_num > 1 else ""
                ch = await guild.create_text_channel(name=f"chat{suffix}", category=category, overwrites=overwrites)
                position = new_num
        except Exception as e:
            embed = err_embed(f"Не удалось создать канал: {e}")
            view = BackToProfileView(self.bot, self.stack_id, self.invoker_id, self.perm_level, self.is_owner)
            return await inter.edit_original_message(embed=embed, view=view)

        await pool.execute(
            "INSERT INTO stack_channels (stack_id, channel_id, channel_type, position) VALUES ($1, $2, $3, $4)",
            self.stack_id, ch.id, ch_type, position
        )

        parent_view = ActiveStackActionsView(self.bot, self.stack_id, self.invoker_id, self.perm_level, self.is_owner)
        embed = await _build_profile_embed(self.bot, self.stack_id)
        await inter.edit_original_message(embed=embed, view=parent_view)


class ChannelDeletePickerView(disnake.ui.View):
    PER_PAGE = 25

    def __init__(self, bot, stack_id, invoker_id, perm_level, is_owner, ch_type: str, items: list, parent, page: int = 0):
        super().__init__(timeout=180)
        self.bot = bot
        self.stack_id = stack_id
        self.invoker_id = invoker_id
        self.perm_level = perm_level
        self.is_owner = is_owner
        self.ch_type = ch_type
        self.items = items
        self.parent = parent
        self.page = page
        self._rebuild()

    async def interaction_check(self, inter):
        return inter.author.id == self.invoker_id

    def _total_pages(self):
        if not self.items:
            return 1
        return (len(self.items) - 1) // self.PER_PAGE + 1

    def _build_embed(self):
        type_name = "голосового" if self.ch_type == "voice" else "текстового"
        return info_embed(
            f"Удаление {type_name} канала • Стр. {self.page+1}/{self._total_pages()}",
            f"Всего каналов этого типа: **{len(self.items)}**\n\n⚠️ Канал будет удалён **немедленно и навсегда** вместе со всем содержимым."
        )

    def _rebuild(self):
        self.clear_items()
        total_pages = self._total_pages()
        if self.page >= total_pages:
            self.page = total_pages - 1
        if self.page < 0:
            self.page = 0
        start = self.page * self.PER_PAGE
        end = start + self.PER_PAGE
        page_items = self.items[start:end]

        if page_items:
            options = [
                disnake.SelectOption(label=item['name'][:80], description=f"ID: {item['channel_id']}", value=str(item['channel_id']))
                for item in page_items
            ]
            sel = disnake.ui.StringSelect(placeholder="Выбрать канал для удаления", options=options, row=0)
            sel.callback = self._on_select
            self.add_item(sel)

        if total_pages > 1:
            prev_b = disnake.ui.Button(label="◀", style=disnake.ButtonStyle.secondary, row=1, disabled=self.page == 0)
            prev_b.callback = self._prev
            self.add_item(prev_b)
            page_b = disnake.ui.Button(label=f"Стр. {self.page+1}/{total_pages}", style=disnake.ButtonStyle.grey, disabled=True, row=1)
            self.add_item(page_b)
            next_b = disnake.ui.Button(label="▶", style=disnake.ButtonStyle.secondary, row=1, disabled=self.page >= total_pages - 1)
            next_b.callback = self._next
            self.add_item(next_b)

        back_b = disnake.ui.Button(label="Назад", style=disnake.ButtonStyle.grey, emoji="↩️", row=2)
        back_b.callback = self._back
        self.add_item(back_b)

    async def _on_select(self, inter):
        ch_id = int(inter.data.values[0])
        item = next((x for x in self.items if x['channel_id'] == ch_id), None)
        if not item:
            return await inter.response.send_message(embed=err_embed("Канал не найден в списке."), ephemeral=True)
        view = ConfirmChannelDeleteView(
            self.bot, self.stack_id, self.invoker_id, self.perm_level, self.is_owner,
            ch_type=self.ch_type, channel_id=ch_id, channel_name=item['name'], parent=self
        )
        embed = disnake.Embed(
            title="—・⚠️ Удаление канала",
            description=(
                f"Вы собираетесь **навсегда удалить** канал {item['mention']}.\n"
                f"Все сообщения и история будут потеряны.\n\n"
                f"**Продолжить?**"
            ),
            color=COLOR_DANGER
        )
        await inter.response.edit_message(embed=embed, view=view)

    async def _prev(self, inter):
        self.page -= 1
        self._rebuild()
        await inter.response.edit_message(embed=self._build_embed(), view=self)

    async def _next(self, inter):
        self.page += 1
        self._rebuild()
        await inter.response.edit_message(embed=self._build_embed(), view=self)

    async def _back(self, inter):
        embed = await _build_profile_embed(self.bot, self.stack_id)
        await inter.response.edit_message(embed=embed, view=self.parent)


class ConfirmChannelDeleteView(disnake.ui.View):
    def __init__(self, bot, stack_id, invoker_id, perm_level, is_owner, ch_type: str, channel_id: int, channel_name: str, parent):
        super().__init__(timeout=60)
        self.bot = bot
        self.stack_id = stack_id
        self.invoker_id = invoker_id
        self.perm_level = perm_level
        self.is_owner = is_owner
        self.ch_type = ch_type
        self.channel_id = channel_id
        self.channel_name = channel_name
        self.parent = parent

    async def interaction_check(self, inter):
        return inter.author.id == self.invoker_id

    @disnake.ui.button(label="Да, удалить", style=disnake.ButtonStyle.danger, emoji="🗑️")
    async def confirm(self, button, inter):
        await inter.response.defer()
        pool = self.bot.pool

        count = await pool.fetchval(
            "SELECT COUNT(*) FROM stack_channels WHERE stack_id = $1 AND channel_type = $2",
            self.stack_id, self.ch_type
        ) or 0
        if count <= 1:
            type_name = "голосовой" if self.ch_type == "voice" else "текстовый"
            embed = err_embed(f"Нельзя удалить последний {type_name} канал стака.")
            return await inter.edit_original_message(embed=embed, view=BackToProfileView(self.bot, self.stack_id, self.invoker_id, self.perm_level, self.is_owner))

        ch = inter.guild.get_channel(self.channel_id)
        if ch:
            try:
                await ch.delete(reason=f"Удаление канала из стака (инициатор: {inter.author})")
            except Exception as e:
                embed = err_embed(f"Не удалось удалить канал в Discord: {e}")
                return await inter.edit_original_message(embed=embed, view=BackToProfileView(self.bot, self.stack_id, self.invoker_id, self.perm_level, self.is_owner))

        await pool.execute(
            "DELETE FROM stack_channels WHERE stack_id = $1 AND channel_id = $2",
            self.stack_id, self.channel_id
        )

        embed = await _build_profile_embed(self.bot, self.stack_id)
        await inter.edit_original_message(embed=embed, view=ActiveStackActionsView(self.bot, self.stack_id, self.invoker_id, self.perm_level, self.is_owner))

    @disnake.ui.button(label="Отмена", style=disnake.ButtonStyle.grey, emoji="↩️")
    async def cancel(self, button, inter):
        await inter.response.edit_message(embed=self.parent._build_embed(), view=self.parent)


class KickSelectView(disnake.ui.View):
    PER_PAGE = 25

    def __init__(self, bot, stack_id, invoker_id, member_ids, perm_level, is_owner, guild, page: int = 0):
        super().__init__(timeout=180)
        self.bot = bot
        self.stack_id = stack_id
        self.invoker_id = invoker_id
        self.perm_level = perm_level
        self.is_owner = is_owner
        self.guild = guild
        self.all_items = []
        for uid in member_ids:
            m = guild.get_member(uid) if guild else None
            label = m.display_name if m else f"Пользователь {uid}"
            self.all_items.append({"user_id": uid, "label": label})
        self.page = page
        self._rebuild()

    async def interaction_check(self, inter):
        return inter.author.id == self.invoker_id

    def _total_pages(self):
        if not self.all_items:
            return 1
        return (len(self.all_items) - 1) // self.PER_PAGE + 1

    def _rebuild(self):
        self.clear_items()
        total_pages = self._total_pages()
        if self.page >= total_pages:
            self.page = total_pages - 1
        if self.page < 0:
            self.page = 0
        start = self.page * self.PER_PAGE
        end = start + self.PER_PAGE
        page_items = self.all_items[start:end]

        if page_items:
            options = [
                disnake.SelectOption(
                    label=item['label'][:80],
                    description=f"ID: {item['user_id']}",
                    value=str(item['user_id'])
                )
                for item in page_items
            ]
            sel = disnake.ui.StringSelect(
                placeholder=f"Выбрать участника для кика (стр. {self.page+1}/{total_pages})",
                options=options,
                row=0
            )
            sel.callback = self._on_select
            self.add_item(sel)

        if total_pages > 1:
            prev_b = disnake.ui.Button(label="◀", style=disnake.ButtonStyle.secondary, row=1, disabled=self.page == 0)
            prev_b.callback = self._prev
            self.add_item(prev_b)
            page_b = disnake.ui.Button(
                label=f"Стр. {self.page+1}/{total_pages}",
                style=disnake.ButtonStyle.grey, disabled=True, row=1
            )
            self.add_item(page_b)
            next_b = disnake.ui.Button(
                label="▶", style=disnake.ButtonStyle.secondary,
                row=1, disabled=self.page >= total_pages - 1
            )
            next_b.callback = self._next
            self.add_item(next_b)

        back_b = disnake.ui.Button(label="Назад", style=disnake.ButtonStyle.grey, emoji="↩️", row=2)
        back_b.callback = self._back
        self.add_item(back_b)

    async def _on_select(self, inter):
        await inter.response.defer()

        uid = int(inter.data.values[0])
        pool = self.bot.pool
        stack = await pool.fetchrow("SELECT role_id, stack_name FROM stacks WHERE stack_id = $1", self.stack_id)
        if not stack:
            embed = err_embed("Стак не найден.")
            view = BackToProfileView(self.bot, self.stack_id, self.invoker_id, self.perm_level, self.is_owner)
            return await inter.edit_original_message(embed=embed, view=view)

        member = inter.guild.get_member(uid)
        if member:
            role = inter.guild.get_role(stack['role_id'])
            if role:
                try:
                    await member.remove_roles(role, reason=f"Кик из стака (инициатор: {inter.author})")
                except Exception:
                    pass

        await pool.execute("DELETE FROM stack_members WHERE stack_id = $1 AND user_id = $2", self.stack_id, uid)
        await pool.execute("DELETE FROM stack_reviewers WHERE stack_id = $1 AND user_id = $2", self.stack_id, uid)

        try:
            user = inter.guild.get_member(uid) or await self.bot.fetch_user(uid)
            await user.send(embed=err_embed(f"Вы были исключены из стака **{stack['stack_name']}**."))
        except Exception:
            pass

        parent_view = ActiveStackActionsView(self.bot, self.stack_id, self.invoker_id, self.perm_level, self.is_owner)
        embed = await _build_profile_embed(self.bot, self.stack_id)
        await inter.edit_original_message(embed=embed, view=parent_view)

    async def _prev(self, inter):
        self.page -= 1
        self._rebuild()
        await inter.response.edit_message(view=self)

    async def _next(self, inter):
        self.page += 1
        self._rebuild()
        await inter.response.edit_message(view=self)

    async def _back(self, inter):
        parent_view = ActiveStackActionsView(self.bot, self.stack_id, self.invoker_id, self.perm_level, self.is_owner)
        embed = await _build_profile_embed(self.bot, self.stack_id)
        await inter.response.edit_message(embed=embed, view=parent_view)


class ConfirmArchiveView(disnake.ui.View):
    def __init__(self, bot, stack_id, invoker_id, perm_level, is_owner):
        super().__init__(timeout=60)
        self.bot = bot
        self.stack_id = stack_id
        self.invoker_id = invoker_id
        self.perm_level = perm_level
        self.is_owner = is_owner

    async def interaction_check(self, inter):
        return inter.author.id == self.invoker_id

    @disnake.ui.button(label="Да, архивировать", style=disnake.ButtonStyle.danger, emoji="📦")
    async def confirm(self, button, inter):
        await inter.response.defer()
        try:
            await _archive_stack(self.bot, self.stack_id, inter.guild, inter.author.id)
            await refresh_applications_panel(self.bot, inter.guild)
            embed = ok_embed(
                "📦 Стак успешно отправлен в архив.\n"
                "Через **30 дней** он будет автоматически удалён.\n"
                "Восстановить можно через `/архив_стаков`."
            )
            await inter.edit_original_message(embed=embed, view=None)
        except Exception as e:
            embed = err_embed(f"Не удалось архивировать: {e}")
            await inter.edit_original_message(embed=embed, view=None)

    @disnake.ui.button(label="Отмена", style=disnake.ButtonStyle.grey, emoji="↩️")
    async def cancel(self, button, inter):
        parent_view = ActiveStackActionsView(self.bot, self.stack_id, self.invoker_id, self.perm_level, self.is_owner)
        embed = await _build_profile_embed(self.bot, self.stack_id)
        await inter.response.edit_message(embed=embed, view=parent_view)


class ConfirmDissolveView(disnake.ui.View):
    def __init__(self, bot, stack_id, invoker_id, perm_level, is_owner):
        super().__init__(timeout=60)
        self.bot = bot
        self.stack_id = stack_id
        self.invoker_id = invoker_id
        self.perm_level = perm_level
        self.is_owner = is_owner

    async def interaction_check(self, inter):
        return inter.author.id == self.invoker_id

    @disnake.ui.button(label="Да, распустить навсегда", style=disnake.ButtonStyle.danger, emoji="💥")
    async def confirm(self, button, inter):
        await inter.response.defer()
        pool = self.bot.pool
        stack_info = await pool.fetchrow("SELECT leader_id, stack_name FROM stacks WHERE stack_id = $1", self.stack_id)
        try:
            await _hard_delete_stack(self.bot, self.stack_id, inter.guild)
            await refresh_applications_panel(self.bot, inter.guild)
            embed = ok_embed(f"💥 Стак **{stack_info['stack_name'] if stack_info else self.stack_id}** распущен.")
            await inter.edit_original_message(embed=embed, view=None)
            if stack_info:
                try:
                    leader = inter.guild.get_member(stack_info['leader_id']) or await self.bot.fetch_user(stack_info['leader_id'])
                    await leader.send(embed=err_embed(
                        f"Ваш стак **{stack_info['stack_name']}** был распущен администратором."
                    ))
                except Exception:
                    pass
        except Exception as e:
            embed = err_embed(f"Ошибка роспуска: {e}")
            await inter.edit_original_message(embed=embed, view=None)

    @disnake.ui.button(label="Отмена", style=disnake.ButtonStyle.grey, emoji="↩️")
    async def cancel(self, button, inter):
        parent_view = ActiveStackActionsView(self.bot, self.stack_id, self.invoker_id, self.perm_level, self.is_owner)
        embed = await _build_profile_embed(self.bot, self.stack_id)
        await inter.response.edit_message(embed=embed, view=parent_view)


class StackOwnerChoiceView(disnake.ui.View):
    def __init__(self, bot, invoker_id, own_stack_id, perm_level):
        super().__init__(timeout=180)
        self.bot = bot
        self.invoker_id = invoker_id
        self.own_stack_id = own_stack_id
        self.perm_level = perm_level

    async def interaction_check(self, inter):
        return inter.author.id == self.invoker_id

    @disnake.ui.button(label="Управлять своим стаком", style=disnake.ButtonStyle.primary, emoji="👑", row=0)
    async def own(self, button, inter):
        embed = await _build_profile_embed(self.bot, self.own_stack_id)
        view = ActiveStackActionsView(self.bot, self.own_stack_id, self.invoker_id, self.perm_level, is_owner=True)
        await inter.response.edit_message(embed=embed, view=view)

    @disnake.ui.button(label="Управлять чужим стаком", style=disnake.ButtonStyle.secondary, emoji="🔧", row=0)
    async def other(self, button, inter):
        pool = self.bot.pool
        rows = await pool.fetch(
            "SELECT stack_id, stack_name, leader_id FROM stacks WHERE guild_id = $1 AND status = 'active' AND stack_id != $2 ORDER BY stack_name",
            inter.guild.id, self.own_stack_id
        )
        if not rows:
            embed = err_embed("На сервере нет других активных стаков.")
            view = _SingleBackView(self._restore_choice, "Назад")
            return await inter.response.edit_message(embed=embed, view=view)
        view = OtherStackPickView(self.bot, self.invoker_id, rows, self.perm_level, parent_restore=self._restore_choice)
        embed = info_embed("Выбор чужого стака", "Выберите стак для управления:")
        await inter.response.edit_message(embed=embed, view=view)

    @disnake.ui.button(label="Проверить тег во всех стаках", style=disnake.ButtonStyle.secondary, emoji="🏷️", row=1)
    async def check_tags(self, button, inter):
        await inter.response.defer(ephemeral=True, with_message=True)
        await _run_check_tags_all(self.bot, inter)

    @disnake.ui.button(label="Закрыть", style=disnake.ButtonStyle.grey, emoji="✖️", row=1)
    async def close(self, button, inter):
        embed = info_embed("Меню закрыто")
        await inter.response.edit_message(embed=embed, view=None)

    async def _restore_choice(self, inter):
        embed = disnake.Embed(
            title="—・Управление стаками",
            description="Вы — **администратор**. Что хотите сделать?",
            color=COLOR_NEUTRAL
        )
        await inter.response.edit_message(embed=embed, view=self)


class AdminNoOwnChoiceView(disnake.ui.View):
    def __init__(self, bot, invoker_id, all_stacks, perm_level):
        super().__init__(timeout=180)
        self.bot = bot
        self.invoker_id = invoker_id
        self.all_stacks = all_stacks
        self.perm_level = perm_level

    async def interaction_check(self, inter):
        return inter.author.id == self.invoker_id

    @disnake.ui.button(label="Управлять чужим стаком", style=disnake.ButtonStyle.primary, emoji="🔧", row=0)
    async def pick(self, button, inter):
        view = OtherStackPickView(self.bot, self.invoker_id, self.all_stacks, self.perm_level, parent_restore=self._restore_choice)
        embed = info_embed("Выбор стака", "Выберите стак для управления:")
        await inter.response.edit_message(embed=embed, view=view)

    @disnake.ui.button(label="Проверить тег во всех стаках", style=disnake.ButtonStyle.secondary, emoji="🏷️", row=0)
    async def check_tags(self, button, inter):
        await inter.response.defer(ephemeral=True, with_message=True)
        await _run_check_tags_all(self.bot, inter)

    @disnake.ui.button(label="Закрыть", style=disnake.ButtonStyle.grey, emoji="✖️", row=1)
    async def close(self, button, inter):
        embed = info_embed("Меню закрыто")
        await inter.response.edit_message(embed=embed, view=None)

    async def _restore_choice(self, inter):
        embed = disnake.Embed(
            title="—・Управление стаками",
            description="Вы — **администратор**. Что хотите сделать?",
            color=COLOR_NEUTRAL
        )
        await inter.response.edit_message(embed=embed, view=self)


class _SingleBackView(disnake.ui.View):
    def __init__(self, back_callback, label="Назад"):
        super().__init__(timeout=180)
        self._cb = back_callback

        btn = disnake.ui.Button(label=label, style=disnake.ButtonStyle.grey, emoji="↩️")
        btn.callback = self._cb
        self.add_item(btn)


class OtherStackPickView(disnake.ui.View):
    def __init__(self, bot, invoker_id, stacks_rows, perm_level, parent_restore=None):
        super().__init__(timeout=180)
        self.bot = bot
        self.invoker_id = invoker_id
        self.perm_level = perm_level
        self.all_stacks = stacks_rows
        self.parent_restore = parent_restore
        self.page = 0
        self.per_page = 25
        self._rebuild()

    async def interaction_check(self, inter):
        return inter.author.id == self.invoker_id

    def _rebuild(self):
        self.clear_items()
        total = len(self.all_stacks)
        total_pages = max(1, (total - 1) // self.per_page + 1)
        if self.page >= total_pages:
            self.page = total_pages - 1
        start = self.page * self.per_page
        end = start + self.per_page
        page_rows = self.all_stacks[start:end]

        if page_rows:
            options = [
                disnake.SelectOption(
                    label=r['stack_name'][:80],
                    description=f"#{r['stack_id']:04d} • лидер ID {r['leader_id']}"[:100],
                    value=str(r['stack_id'])
                )
                for r in page_rows
            ]
            sel = disnake.ui.StringSelect(placeholder=f"Выбрать стак (стр. {self.page+1}/{total_pages})", options=options, row=0)
            sel.callback = self._on_select
            self.add_item(sel)

        if total_pages > 1:
            prev_b = disnake.ui.Button(label="◀", style=disnake.ButtonStyle.secondary, row=1, disabled=self.page == 0)
            prev_b.callback = self._prev
            self.add_item(prev_b)

            info_b = disnake.ui.Button(label=f"Стр. {self.page+1}/{total_pages}", style=disnake.ButtonStyle.grey, disabled=True, row=1)
            self.add_item(info_b)

            next_b = disnake.ui.Button(label="▶", style=disnake.ButtonStyle.secondary, row=1, disabled=self.page >= total_pages - 1)
            next_b.callback = self._next
            self.add_item(next_b)

        if self.parent_restore:
            back_b = disnake.ui.Button(label="Назад", style=disnake.ButtonStyle.grey, emoji="↩️", row=2)
            back_b.callback = self._back_to_parent
            self.add_item(back_b)

    def _get_embed(self):
        total = len(self.all_stacks)
        total_pages = max(1, (total - 1) // self.per_page + 1)
        return info_embed(
            f"Активные стаки • Страница {self.page+1}/{total_pages}",
            f"Всего активных стаков: **{total}**\n\nВыберите стак для управления:"
        )

    async def _on_select(self, inter):
        stack_id = int(inter.data.values[0])
        embed = await _build_profile_embed(self.bot, stack_id)
        view = ActiveStackActionsView(self.bot, stack_id, self.invoker_id, self.perm_level, is_owner=False)
        await inter.response.edit_message(embed=embed, view=view)

    async def _prev(self, inter):
        self.page -= 1
        self._rebuild()
        await inter.response.edit_message(embed=self._get_embed(), view=self)

    async def _next(self, inter):
        self.page += 1
        self._rebuild()
        await inter.response.edit_message(embed=self._get_embed(), view=self)

    async def _back_to_parent(self, inter):
        if self.parent_restore:
            await self.parent_restore(inter)


class ArchiveManagementView(disnake.ui.View):
    def __init__(self, bot, invoker_id, stacks_rows, perm_level):
        super().__init__(timeout=180)
        self.bot = bot
        self.invoker_id = invoker_id
        self.perm_level = perm_level
        self.all_stacks = stacks_rows
        self.page = 0
        self.per_page = 25
        self._rebuild()

    async def interaction_check(self, inter):
        return inter.author.id == self.invoker_id

    def _rebuild(self):
        self.clear_items()
        total = len(self.all_stacks)
        total_pages = max(1, (total - 1) // self.per_page + 1)
        if self.page >= total_pages:
            self.page = total_pages - 1
        start = self.page * self.per_page
        end = start + self.per_page
        page_rows = self.all_stacks[start:end]

        if page_rows:
            options = [
                disnake.SelectOption(
                    label=r['stack_name'][:80],
                    description=f"#{r['stack_id']:04d} • лидер ID {r['leader_id']}"[:100],
                    value=str(r['stack_id'])
                )
                for r in page_rows
            ]
            sel = disnake.ui.StringSelect(placeholder=f"Выбрать стак (стр. {self.page+1}/{total_pages})", options=options, row=0)
            sel.callback = self._on_select
            self.add_item(sel)

        if total_pages > 1:
            prev_b = disnake.ui.Button(label="◀", style=disnake.ButtonStyle.secondary, row=1, disabled=self.page == 0)
            prev_b.callback = self._prev
            self.add_item(prev_b)
            info_b = disnake.ui.Button(label=f"Стр. {self.page+1}/{total_pages}", style=disnake.ButtonStyle.grey, disabled=True, row=1)
            self.add_item(info_b)
            next_b = disnake.ui.Button(label="▶", style=disnake.ButtonStyle.secondary, row=1, disabled=self.page >= total_pages - 1)
            next_b.callback = self._next
            self.add_item(next_b)

        close_b = disnake.ui.Button(label="Закрыть", style=disnake.ButtonStyle.grey, emoji="✖️", row=2)
        close_b.callback = self._close
        self.add_item(close_b)

    def _get_embed(self):
        total = len(self.all_stacks)
        total_pages = max(1, (total - 1) // self.per_page + 1)
        title_suffix = "(ваши)" if self.perm_level == "user" else "(все)"
        return info_embed(
            f"Архив стаков {title_suffix} • Страница {self.page+1}/{total_pages}",
            f"Всего в архиве: **{total}**\n\nВыберите стак для восстановления или удаления:"
        )

    async def _on_select(self, inter):
        stack_id = int(inter.data.values[0])
        embed = await _build_profile_embed(self.bot, stack_id)
        view = ArchivedStackActionsView(self.bot, stack_id, self.invoker_id, self.perm_level, parent=self)
        await inter.response.edit_message(embed=embed, view=view)

    async def _prev(self, inter):
        self.page -= 1
        self._rebuild()
        await inter.response.edit_message(embed=self._get_embed(), view=self)

    async def _next(self, inter):
        self.page += 1
        self._rebuild()
        await inter.response.edit_message(embed=self._get_embed(), view=self)

    async def _close(self, inter):
        embed = info_embed("Меню закрыто")
        await inter.response.edit_message(embed=embed, view=None)


class ArchivedStackActionsView(disnake.ui.View):
    def __init__(self, bot, stack_id, invoker_id, perm_level, parent=None):
        super().__init__(timeout=300)
        self.bot = bot
        self.stack_id = stack_id
        self.invoker_id = invoker_id
        self.perm_level = perm_level
        self.parent = parent

    async def interaction_check(self, inter):
        return inter.author.id == self.invoker_id

    @disnake.ui.button(label="Восстановить", style=disnake.ButtonStyle.success, emoji="♻️", row=0)
    async def restore(self, button, inter):
        await inter.response.defer()
        try:
            result = await _restore_stack(self.bot, self.stack_id, inter.guild)
            await refresh_applications_panel(self.bot, inter.guild)
            desc_parts = [
                "✅ Стак восстановлен.",
                f"\n**Роль:** {result['role'].mention}",
                f"**Каналы:** {', '.join(result['restored_channels'])}",
                f"**Участников возвращено:** {result['members_count']}",
            ]
            if result['failed_channels']:
                desc_parts.append(f"\n⚠️ Не восстановлено: {', '.join(result['failed_channels'])}")

            pool = self.bot.pool
            stack = await pool.fetchrow("SELECT leader_id, stack_name FROM stacks WHERE stack_id = $1", self.stack_id)
            if stack:
                try:
                    leader = inter.guild.get_member(stack['leader_id']) or await self.bot.fetch_user(stack['leader_id'])
                    await leader.send(embed=ok_embed(f"Ваш стак **{stack['stack_name']}** восстановлен из архива!"))
                except Exception:
                    pass

            await inter.edit_original_message(embed=ok_embed("\n".join(desc_parts)), view=None)
        except Exception as e:
            await inter.edit_original_message(embed=err_embed(f"Ошибка восстановления: {e}"), view=None)

    @disnake.ui.button(label="Удалить безвозвратно", style=disnake.ButtonStyle.danger, emoji="🗑️", row=0)
    async def delete_forever(self, button, inter):
        if self.perm_level == "user":
            embed = err_embed("Безвозвратное удаление доступно только администраторам и супер-админам.")
            view = _OneActionView("Назад", self._back)
            return await inter.response.edit_message(embed=embed, view=view)
        view = ConfirmArchivedDeleteView(self.bot, self.stack_id, self.invoker_id, self.perm_level, parent=self.parent)
        embed = disnake.Embed(
            title="—・⚠️ БЕЗВОЗВРАТНОЕ удаление",
            description=(
                "Вы собираетесь **полностью удалить** заархивированный стак.\n"
                "Все связанные данные и каналы (если ещё остались) исчезнут навсегда.\n\n"
                "**Продолжить?**"
            ),
            color=COLOR_DANGER
        )
        await inter.response.edit_message(embed=embed, view=view)

    @disnake.ui.button(label="Назад", style=disnake.ButtonStyle.grey, emoji="↩️", row=1)
    async def back(self, button, inter):
        await self._back(inter)

    async def _back(self, inter):
        if self.parent:
            self.parent._rebuild()
            await inter.response.edit_message(embed=self.parent._get_embed(), view=self.parent)
        else:
            embed = info_embed("Меню закрыто")
            await inter.response.edit_message(embed=embed, view=None)


class ConfirmArchivedDeleteView(disnake.ui.View):
    def __init__(self, bot, stack_id, invoker_id, perm_level, parent=None):
        super().__init__(timeout=60)
        self.bot = bot
        self.stack_id = stack_id
        self.invoker_id = invoker_id
        self.perm_level = perm_level
        self.parent = parent

    async def interaction_check(self, inter):
        return inter.author.id == self.invoker_id

    @disnake.ui.button(label="Да, удалить навсегда", style=disnake.ButtonStyle.danger, emoji="🗑️")
    async def confirm(self, button, inter):
        await inter.response.defer()
        try:
            await _hard_delete_stack(self.bot, self.stack_id, inter.guild)
            await inter.edit_original_message(embed=ok_embed("✅ Стак удалён безвозвратно."), view=None)
        except Exception as e:
            await inter.edit_original_message(embed=err_embed(f"Ошибка удаления: {e}"), view=None)

    @disnake.ui.button(label="Отмена", style=disnake.ButtonStyle.grey, emoji="↩️")
    async def cancel(self, button, inter):
        embed = await _build_profile_embed(self.bot, self.stack_id)
        view = ArchivedStackActionsView(self.bot, self.stack_id, self.invoker_id, self.perm_level, parent=self.parent)
        await inter.response.edit_message(embed=embed, view=view)


class _OneActionView(disnake.ui.View):
    def __init__(self, label, callback):
        super().__init__(timeout=180)
        btn = disnake.ui.Button(label=label, style=disnake.ButtonStyle.grey, emoji="↩️")
        btn.callback = callback
        self.add_item(btn)


class RecruitmentToggleView(disnake.ui.View):
    def __init__(self, bot, stack_id, invoker_id, perm_level, is_owner):
        super().__init__(timeout=180)
        self.bot = bot
        self.stack_id = stack_id
        self.invoker_id = invoker_id
        self.perm_level = perm_level
        self.is_owner = is_owner

    async def interaction_check(self, inter):
        return inter.author.id == self.invoker_id

    async def _build_embed(self):
        row = await self.bot.pool.fetchrow(
            "SELECT stack_name, recruitment_open FROM stacks WHERE stack_id = $1",
            self.stack_id
        )
        if not row:
            return err_embed("Стак не найден.")
        is_open = bool(row['recruitment_open'])
        state_emoji = "🔓" if is_open else "🔒"
        state_text = "**ОТКРЫТ**" if is_open else "**ЗАКРЫТ**"
        return disnake.Embed(
            title="—・🎟️ Набор в стак",
            description=(
                f"**Стак:** {row['stack_name']}\n"
                f"**Текущее состояние:** {state_emoji} {state_text}\n\n"
                + (
                    "_Сейчас стак виден в панели заявок и принимает новые заявки._\n"
                    "_Нажмите кнопку ниже чтобы **закрыть набор** — стак исчезнет из панели "
                    "и новые заявки приниматься не будут._"
                    if is_open else
                    "_Сейчас стак скрыт из панели заявок и не принимает новые заявки._\n"
                    "_Существующие участники остаются. Pending-заявки можно отдельно "
                    "закрыть через раздел \"Заявки\"._\n"
                    "_Нажмите кнопку ниже чтобы **открыть набор** — стак появится в панели заявок._"
                )
            ),
            color=COLOR_OK if is_open else COLOR_NEUTRAL
        )

    @disnake.ui.button(label="Переключить", style=disnake.ButtonStyle.primary, emoji="🔁", row=0)
    async def toggle(self, button, inter):
        await inter.response.defer()
        new_state = await self.bot.pool.fetchval(
            "UPDATE stacks SET recruitment_open = NOT recruitment_open WHERE stack_id = $1 RETURNING recruitment_open",
            self.stack_id
        )
        try:
            await refresh_applications_panel(self.bot, inter.guild)
        except Exception:
            pass
        embed = await self._build_embed()
        await inter.edit_original_message(embed=embed, view=self)

    @disnake.ui.button(label="Назад", style=disnake.ButtonStyle.grey, emoji="↩️", row=0)
    async def back(self, button, inter):
        embed = await _build_profile_embed(self.bot, self.stack_id)
        view = ActiveStackActionsView(
            self.bot, self.stack_id, self.invoker_id, self.perm_level, self.is_owner
        )
        await inter.response.edit_message(embed=embed, view=view)


class PendingApplicationsView(disnake.ui.View):
    PER_PAGE = 10

    def __init__(self, bot, stack_id, invoker_id, perm_level, is_owner, page: int = 0):
        super().__init__(timeout=300)
        self.bot = bot
        self.stack_id = stack_id
        self.invoker_id = invoker_id
        self.perm_level = perm_level
        self.is_owner = is_owner
        self.page = page
        self.applications = []

    async def interaction_check(self, inter):
        return inter.author.id == self.invoker_id

    async def _load(self):
        self.applications = await self.bot.pool.fetch(
            """
            SELECT id, applicant_id, applied_at
            FROM stack_applications
            WHERE stack_id = $1 AND status = 'pending'
            ORDER BY applied_at ASC
            """,
            self.stack_id
        )

    def _total_pages(self):
        if not self.applications:
            return 1
        return (len(self.applications) - 1) // self.PER_PAGE + 1

    async def build_embed(self):
        await self._load()
        total_pages = self._total_pages()
        if self.page >= total_pages:
            self.page = total_pages - 1
        if self.page < 0:
            self.page = 0

        embed = disnake.Embed(
            title="—・📋 Заявки в стак",
            description=(
                f"Pending заявок: **{len(self.applications)}**\n"
                f"Страница: **{self.page + 1}/{total_pages}**\n\n"
                "_Здесь — заявки, которые ещё не приняты и не отклонены._\n"
                "_Чтобы принять — обработайте DM-сообщение. Чтобы закрыть отсюда — выберите её в меню ниже._"
            ),
            color=COLOR_NEUTRAL
        )

        if not self.applications:
            embed.add_field(name="> Пусто", value="\u200b**・** Нет ожидающих заявок", inline=False)
        else:
            start = self.page * self.PER_PAGE
            end = start + self.PER_PAGE
            page_apps = self.applications[start:end]
            for app in page_apps:
                embed.add_field(
                    name=f"> Заявка #{app['id']}",
                    value=(
                        f"\u200b**・** От: <@{app['applicant_id']}>\n"
                        f"\u200b**・** Когда: <t:{app['applied_at']}:R>"
                    ),
                    inline=False
                )

        self.clear_items()
        if self.applications:
            start = self.page * self.PER_PAGE
            end = start + self.PER_PAGE
            page_apps = self.applications[start:end]
            options = [
                disnake.SelectOption(
                    label=f"Закрыть заявку #{app['id']}",
                    description=f"От: {app['applicant_id']}",
                    value=str(app['id']),
                )
                for app in page_apps
            ]
            sel = disnake.ui.StringSelect(
                placeholder="Выбрать заявку для закрытия",
                options=options[:25],
                row=0
            )
            sel.callback = self._on_close_select
            self.add_item(sel)

        if self._total_pages() > 1:
            prev_b = disnake.ui.Button(label="◀", style=disnake.ButtonStyle.secondary, disabled=self.page == 0, row=1)
            prev_b.callback = self._prev
            self.add_item(prev_b)
            page_b = disnake.ui.Button(
                label=f"Стр. {self.page + 1}/{self._total_pages()}",
                style=disnake.ButtonStyle.grey, disabled=True, row=1
            )
            self.add_item(page_b)
            next_b = disnake.ui.Button(
                label="▶", style=disnake.ButtonStyle.secondary,
                disabled=self.page >= self._total_pages() - 1, row=1
            )
            next_b.callback = self._next
            self.add_item(next_b)

        back_b = disnake.ui.Button(label="Назад", style=disnake.ButtonStyle.grey, emoji="↩️", row=2)
        back_b.callback = self._back
        self.add_item(back_b)

        return embed

    async def _on_close_select(self, inter):
        try:
            app_id = int(inter.data.values[0])
        except Exception:
            return await inter.response.send_message(embed=err_embed("Неверный ID."), ephemeral=True)

        app = await self.bot.pool.fetchrow(
            "SELECT applicant_id FROM stack_applications WHERE id = $1 AND stack_id = $2 AND status = 'pending'",
            app_id, self.stack_id
        )
        if not app:
            return await inter.response.send_message(
                embed=err_embed("Заявка уже обработана или не найдена."), ephemeral=True
            )

        now = int(time.time())
        reason = "closed_by_leader" if self.is_owner else "closed_by_reviewer"
        await self.bot.pool.execute(
            "UPDATE stack_applications SET status = 'rejected', processed_at = $1, processed_by = $2, "
            "rejection_reason = $3 WHERE id = $4",
            now, inter.author.id, reason, app_id
        )

        try:
            cog = self.bot.get_cog("Stacks")
            if cog and hasattr(cog, "_update_all_application_messages"):
                role_label = "лидером" if self.is_owner else "проверяющим"
                await cog._update_all_application_messages(
                    app_id, inter.author.id,
                    result_text=f"❌ Закрыто {role_label} <@{inter.author.id}>",
                    color=COLOR_ERR
                )
        except Exception:
            pass

        try:
            applicant = await self.bot.fetch_user(app['applicant_id'])
            stack = await self.bot.pool.fetchrow(
                "SELECT stack_name FROM stacks WHERE stack_id = $1",
                self.stack_id
            )
            if stack:
                await applicant.send(embed=err_embed(
                    f"Ваша заявка в стак **{stack['stack_name']}** была закрыта."
                ))
        except Exception:
            pass

        embed = await self.build_embed()
        await inter.response.edit_message(embed=embed, view=self)

    async def _prev(self, inter):
        self.page -= 1
        embed = await self.build_embed()
        await inter.response.edit_message(embed=embed, view=self)

    async def _next(self, inter):
        self.page += 1
        embed = await self.build_embed()
        await inter.response.edit_message(embed=embed, view=self)

    async def _back(self, inter):
        embed = await _build_profile_embed(self.bot, self.stack_id)
        view = ActiveStackActionsView(
            self.bot, self.stack_id, self.invoker_id, self.perm_level, self.is_owner
        )
        await inter.response.edit_message(embed=embed, view=view)


class ConfirmLeaveStackView(disnake.ui.View):
    def __init__(self, bot, stack_id, user_id):
        super().__init__(timeout=60)
        self.bot = bot
        self.stack_id = stack_id
        self.user_id = user_id

    async def interaction_check(self, inter):
        return inter.author.id == self.user_id

    @disnake.ui.button(label="Да, покинуть стак", style=disnake.ButtonStyle.danger, emoji="🚪")
    async def confirm(self, button, inter):
        await inter.response.defer()
        pool = self.bot.pool
        stack = await pool.fetchrow(
            "SELECT role_id, stack_name, leader_id FROM stacks WHERE stack_id = $1",
            self.stack_id
        )
        if not stack:
            return await inter.edit_original_message(embed=err_embed("Стак не найден."), view=None)
        if stack['leader_id'] == self.user_id:
            return await inter.edit_original_message(
                embed=err_embed(
                    "Лидер не может выйти из своего стака. "
                    "Используйте архивацию или роспуск."
                ),
                view=None
            )

        await pool.execute(
            "DELETE FROM stack_members WHERE stack_id = $1 AND user_id = $2",
            self.stack_id, self.user_id
        )
        await pool.execute(
            "DELETE FROM stack_reviewers WHERE stack_id = $1 AND user_id = $2",
            self.stack_id, self.user_id
        )

        member = inter.guild.get_member(self.user_id)
        if member and stack['role_id']:
            role = inter.guild.get_role(stack['role_id'])
            if role:
                try:
                    await member.remove_roles(role, reason="Покинул стак по своей воле")
                except Exception:
                    pass

        try:
            leader = inter.guild.get_member(stack['leader_id']) or await self.bot.fetch_user(stack['leader_id'])
            await leader.send(embed=info_embed(
                "Участник покинул стак",
                f"<@{self.user_id}> покинул ваш стак **{stack['stack_name']}**."
            ))
        except Exception:
            pass

        await inter.edit_original_message(
            embed=ok_embed(f"Вы покинули стак **{stack['stack_name']}**."),
            view=None
        )

    @disnake.ui.button(label="Отмена", style=disnake.ButtonStyle.grey, emoji="↩️")
    async def cancel(self, button, inter):
        embed = await _build_profile_embed(self.bot, self.stack_id)
        view = MemberProfileView(self.bot, self.stack_id, self.user_id)
        await inter.response.edit_message(embed=embed, view=view)


class MemberProfileView(disnake.ui.View):
    def __init__(self, bot, stack_id, invoker_id):
        super().__init__(timeout=300)
        self.bot = bot
        self.stack_id = stack_id
        self.invoker_id = invoker_id

    async def interaction_check(self, inter):
        return inter.author.id == self.invoker_id

    @disnake.ui.button(label="Покинуть стак", style=disnake.ButtonStyle.danger, emoji="🚪", row=0)
    async def leave(self, button, inter):
        view = ConfirmLeaveStackView(self.bot, self.stack_id, self.invoker_id)
        embed = disnake.Embed(
            title="—・🚪 Выход из стака",
            description=(
                "Вы собираетесь покинуть стак.\n"
                "С вас будет снята роль стака. Чтобы вернуться — нужно будет подать заявку заново.\n\n"
                "**Продолжить?**"
            ),
            color=COLOR_WARN
        )
        await inter.response.edit_message(embed=embed, view=view)

    @disnake.ui.button(label="Закрыть", style=disnake.ButtonStyle.grey, emoji="✖️", row=0)
    async def close(self, button, inter):
        await inter.response.edit_message(
            embed=info_embed("Закрыто", "Меню профиля закрыто."),
            view=None
        )


class Stacks(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._views_added = False

    @commands.Cog.listener()
    async def on_ready(self):
        if self._views_added:
            return
        self._views_added = True
        self.bot.add_view(ApprovalView())
        self.bot.add_view(LeaderReviewView())
        for guild in self.bot.guilds:
            try:
                pool = self.bot.pool
                stacks_list = await pool.fetch(
                    "SELECT stack_id, stack_name FROM stacks WHERE guild_id = $1 AND status = 'active' "
                    "AND recruitment_open = TRUE ORDER BY stack_name",
                    guild.id
                )
                self.bot.add_view(ApplicationsPanelView(stacks_list))
                await refresh_applications_panel(self.bot, guild)
            except Exception as e:
                print(f"[STACKS] on_ready error: {e}", flush=True)

    @commands.slash_command(name="создать_стак", description="Создать новый стак")
    async def create_stack(self, inter, название: str):
        await inter.response.defer(ephemeral=True, with_message=True)
        cfg = await load_config()
        s = cfg.settings

        if not s.stacks.approval_log_channel_id:
            return await inter.edit_original_response(embed=err_embed("Лог-канал одобрения не настроен."))

        name = название.strip()
        if not NAME_REGEX.match(name):
            return await inter.edit_original_response(embed=err_embed("Имя 2–32 символа. Разрешены буквы, цифры, пробелы, `-`, `_` и эмодзи."))

        pool = self.bot.pool

        exists_leader = await pool.fetchrow(
            "SELECT stack_id, status FROM stacks WHERE leader_id = $1 AND status IN ('pending_approval', 'active')",
            inter.author.id
        )
        if exists_leader:
            st = "ожидает одобрения" if exists_leader['status'] == STATUS_PENDING else "уже активен"
            return await inter.edit_original_response(embed=err_embed(f"У вас уже есть стак (#{exists_leader['stack_id']:04d}), он {st}."))

        exists_member = await pool.fetchrow(
            """
            SELECT s.stack_id, s.stack_name FROM stack_members m
            JOIN stacks s ON s.stack_id = m.stack_id
            WHERE m.user_id = $1 AND s.status = 'active'
            """,
            inter.author.id
        )
        if exists_member:
            return await inter.edit_original_response(embed=err_embed(f"Вы уже участник стака **{exists_member['stack_name']}**."))

        exists_name = await pool.fetchrow(
            "SELECT stack_id FROM stacks WHERE guild_id = $1 AND lower(stack_name) = lower($2) AND status IN ('pending_approval', 'active')",
            inter.guild.id, name
        )
        if exists_name:
            return await inter.edit_original_response(embed=err_embed(f"Стак с именем **{name}** уже существует."))

        now = int(time.time())
        stack_id = await pool.fetchval(
            """
            INSERT INTO stacks (guild_id, leader_id, stack_name, status, created_at)
            VALUES ($1, $2, $3, 'pending_approval', $4)
            RETURNING stack_id
            """,
            inter.guild.id, inter.author.id, name, now
        )

        log_ch = inter.guild.get_channel(s.stacks.approval_log_channel_id)
        if not log_ch:
            await pool.execute("DELETE FROM stacks WHERE stack_id = $1", stack_id)
            return await inter.edit_original_response(embed=err_embed("Лог-канал не найден."))

        embed = disnake.Embed(title=f"—・Новый стак | #{stack_id:04d}", color=COLOR_WARN)
        embed.add_field(name="> Лидер:", value=f"\u200b**・** {inter.author.mention} (`{inter.author.id}`)", inline=True)
        embed.add_field(name="> Название:", value=f"\u200b**・** **{name}**", inline=True)
        embed.add_field(name="> Дата:", value=f"\u200b**・** <t:{now}:F>", inline=False)
        embed.add_field(name="> Статус:", value="\u200b**・** ⏳ Ожидает одобрения", inline=False)
        embed.set_thumbnail(url=inter.author.display_avatar.url)

        try:
            msg = await log_ch.send(embed=embed, view=ApprovalView())
        except Exception:
            await pool.execute("DELETE FROM stacks WHERE stack_id = $1", stack_id)
            return await inter.edit_original_response(embed=err_embed("Не удалось отправить сообщение в лог-канал."))

        await pool.execute(
            "INSERT INTO stack_log_msgs (stack_id, channel_id, message_id) VALUES ($1, $2, $3)",
            stack_id, log_ch.id, msg.id
        )

        await inter.edit_original_response(embed=ok_embed(f"Заявка на стак **{name}** отправлена на рассмотрение."))

    @commands.slash_command(name="выйти_из_стака", description="Покинуть свой текущий стак")
    async def leave_stack(self, inter):
        await inter.response.defer(ephemeral=True, with_message=True)
        pool = self.bot.pool
        row = await pool.fetchrow(
            """
            SELECT s.stack_id, s.stack_name, s.leader_id FROM stack_members m
            JOIN stacks s ON s.stack_id = m.stack_id
            WHERE m.user_id = $1 AND s.guild_id = $2 AND s.status = 'active'
            """,
            inter.author.id, inter.guild.id
        )
        if not row:
            return await inter.edit_original_response(
                embed=err_embed("Вы не состоите ни в одном активном стаке.")
            )
        if row['leader_id'] == inter.author.id:
            return await inter.edit_original_response(
                embed=err_embed(
                    "Лидер не может выйти из своего стака. "
                    "Используйте `/управление_стаком` → Архивировать."
                )
            )
        view = ConfirmLeaveStackView(self.bot, row['stack_id'], inter.author.id)
        embed = disnake.Embed(
            title="—・🚪 Выход из стака",
            description=(
                f"Вы собираетесь покинуть стак **{row['stack_name']}**.\n"
                "С вас будет снята роль стака.\n\n"
                "**Продолжить?**"
            ),
            color=COLOR_WARN
        )
        await inter.edit_original_response(embed=embed, view=view)

    @commands.Cog.listener()
    async def on_interaction(self, inter):
        if inter.type != disnake.InteractionType.component:
            return
        cid = inter.data.custom_id if inter.data else None
        if not cid:
            return

        if cid in ("stack_approve", "stack_reject"):
            await self._handle_approval(inter, cid)
        elif cid.startswith("stacks_apply_select_p"):
            try:
                stack_id = int(inter.data.values[0])
            except Exception:
                return
            await self._handle_apply_by_id(inter, stack_id)
        elif cid.startswith("stacks_apply_prev_p") or cid.startswith("stacks_apply_next_p"):
            await self._handle_panel_pagination(inter, cid)
        elif cid.startswith("stack_apply_"):
            await self._handle_apply(inter, cid)
        elif cid in ("app_accept", "app_reject"):
            await self._handle_leader_review(inter, cid)

    async def _handle_panel_pagination(self, inter, cid):
        try:
            current_page = int(cid.rsplit("_p", 1)[1])
        except Exception:
            return
        new_page = current_page - 1 if "prev" in cid else current_page + 1
        pool = self.bot.pool
        stacks_list = await pool.fetch(
            "SELECT stack_id, stack_name FROM stacks WHERE guild_id = $1 AND status = 'active' "
            "AND recruitment_open = TRUE ORDER BY stack_name",
            inter.guild.id
        )
        if not stacks_list:
            return await inter.response.send_message(embed=err_embed("Активных стаков нет."), ephemeral=True)
        new_view = ApplicationsPanelView(stacks_list, page=new_page)
        new_embed = _build_panel_embed(stacks_list)
        try:
            await inter.response.edit_message(embed=new_embed, view=new_view)
        except Exception:
            try:
                if not inter.response.is_done():
                    await inter.response.defer()
                await inter.edit_original_message(embed=new_embed, view=new_view)
            except Exception:
                pass

    async def _handle_apply_by_id(self, inter, stack_id: int):
        passed, retry = await self.bot.check_cooldown_redis(inter.author.id, "stack_apply", 5.0)
        if not passed:
            return await inter.response.send_message(
                embed=disnake.Embed(title="⏳ Анти-Спам", description=f"Ждите {retry} сек.", color=COLOR_WARN),
                ephemeral=True
            )
        await inter.response.defer(ephemeral=True, with_message=True)
        await self._apply_to_stack(inter, stack_id)

    async def _handle_approval(self, inter, cid):
        passed, retry = await self.bot.check_cooldown_redis(inter.author.id, "stack_moderation", 2.0)
        if not passed:
            if not inter.response.is_done():
                await inter.response.send_message(embed=disnake.Embed(title="⏳ Анти-Спам", description=f"Ждите {retry} сек.", color=COLOR_WARN), ephemeral=True)
            return

        if not await is_admin_user(inter.author.id, [r.id for r in inter.author.roles]):
            if not inter.response.is_done():
                await inter.response.send_message(embed=err_embed("Только администраторы могут одобрять стаки."), ephemeral=True)
            return

        await inter.response.defer(ephemeral=True, with_message=True)

        pool = self.bot.pool
        row = await pool.fetchrow(
            """
            SELECT s.stack_id, s.leader_id, s.stack_name, s.status
            FROM stack_log_msgs m JOIN stacks s ON s.stack_id = m.stack_id
            WHERE m.message_id = $1
            """,
            inter.message.id
        )
        if not row:
            return await inter.edit_original_response(embed=err_embed("Стак не найден."))
        if row['status'] != STATUS_PENDING:
            return await inter.edit_original_response(embed=err_embed(f"Стак уже в статусе `{row['status']}`."))

        try:
            if cid == "stack_approve":
                await self._approve_stack(inter, row)
                await refresh_applications_panel(self.bot, inter.guild)
                await inter.edit_original_response(embed=ok_embed(f"Стак **{row['stack_name']}** одобрен."))
            else:
                await self._reject_stack(inter, row)
                await inter.edit_original_response(embed=ok_embed("Заявка отклонена."))
        except Exception as e:
            await inter.edit_original_response(embed=err_embed(f"Ошибка: {e}"))

    async def _approve_stack(self, inter, row):
        cfg = await load_config()
        pool = self.bot.pool
        guild = inter.guild

        role = await guild.create_role(name=row['stack_name'], reason=f"Стак #{row['stack_id']}")
        leader_member = guild.get_member(row['leader_id'])
        if leader_member:
            try:
                await leader_member.add_roles(role, reason="Лидер стака")
            except Exception:
                pass

        overwrites = {
            guild.default_role: disnake.PermissionOverwrite(view_channel=False, connect=False),
            role: disnake.PermissionOverwrite(view_channel=True, connect=True, send_messages=True),
            guild.me: disnake.PermissionOverwrite(view_channel=True, connect=True, manage_channels=True, manage_roles=True),
        }

        category = await guild.create_category(name=row['stack_name'], overwrites=overwrites, reason=f"Стак #{row['stack_id']}")

        text_ch = await guild.create_text_channel(name="chat", category=category, overwrites=overwrites)
        voice_channels = []
        for i in range(1, cfg.settings.stacks.default_voice_count + 1):
            vc = await guild.create_voice_channel(name=f"Voice {i}", category=category, overwrites=overwrites)
            voice_channels.append(vc)

        now = int(time.time())
        await pool.execute(
            "UPDATE stacks SET status = 'active', role_id = $1, category_id = $2, approved_at = $3 WHERE stack_id = $4",
            role.id, category.id, now, row['stack_id']
        )
        await pool.execute(
            "INSERT INTO stack_channels (stack_id, channel_id, channel_type, position) VALUES ($1, $2, 'text', 0)",
            row['stack_id'], text_ch.id
        )
        for i, vc in enumerate(voice_channels, start=1):
            await pool.execute(
                "INSERT INTO stack_channels (stack_id, channel_id, channel_type, position) VALUES ($1, $2, 'voice', $3)",
                row['stack_id'], vc.id, i
            )
        await pool.execute(
            "INSERT INTO stack_members (stack_id, user_id, joined_at, added_by) VALUES ($1, $2, $3, $4) ON CONFLICT DO NOTHING",
            row['stack_id'], row['leader_id'], now, row['leader_id']
        )

        embed = inter.message.embeds[0]
        embed.color = COLOR_OK
        embed.clear_fields()
        embed.add_field(name="> Лидер:", value=f"\u200b**・** <@{row['leader_id']}>", inline=True)
        embed.add_field(name="> Название:", value=f"\u200b**・** **{row['stack_name']}**", inline=True)
        embed.add_field(name="> Роль:", value=f"\u200b**・** {role.mention}", inline=True)
        embed.add_field(name="> Каналы:", value=f"\u200b**・** {text_ch.mention}\n\u200b**・** {', '.join(vc.mention for vc in voice_channels)}", inline=False)
        embed.add_field(name="> Статус:", value=f"\u200b**・** ✅ Одобрил: {inter.author.mention}", inline=False)
        try:
            await inter.message.edit(embed=embed, view=None)
        except Exception:
            pass

        try:
            leader = guild.get_member(row['leader_id']) or await self.bot.fetch_user(row['leader_id'])
            await leader.send(embed=ok_embed(f"Ваш стак **{row['stack_name']}** одобрен!"))
        except Exception:
            pass

    async def _reject_stack(self, inter, row):
        pool = self.bot.pool
        await pool.execute("DELETE FROM stacks WHERE stack_id = $1", row['stack_id'])
        embed = inter.message.embeds[0]
        embed.color = COLOR_ERR
        embed.add_field(name="> Статус:", value=f"\u200b**・** ❌ Отклонил: {inter.author.mention}", inline=False)
        try:
            await inter.message.edit(embed=embed, view=None)
        except Exception:
            pass
        try:
            leader = inter.guild.get_member(row['leader_id']) or await self.bot.fetch_user(row['leader_id'])
            await leader.send(embed=err_embed(f"Ваша заявка на стак **{row['stack_name']}** отклонена."))
        except Exception:
            pass

    async def _handle_apply(self, inter, cid):
        passed, retry = await self.bot.check_cooldown_redis(inter.author.id, "stack_apply", 5.0)
        if not passed:
            return await inter.response.send_message(
                embed=disnake.Embed(title="⏳ Анти-Спам", description=f"Ждите {retry} сек.", color=COLOR_WARN),
                ephemeral=True
            )
        await inter.response.defer(ephemeral=True, with_message=True)
        try:
            stack_id = int(cid.split("_")[-1])
        except Exception:
            return await inter.edit_original_response(embed=err_embed("Некорректный ID."))
        await self._apply_to_stack(inter, stack_id)

    async def _apply_to_stack(self, inter, stack_id: int):
        pool = self.bot.pool
        stack = await pool.fetchrow(
            "SELECT leader_id, stack_name, status, recruitment_open FROM stacks WHERE stack_id = $1",
            stack_id
        )
        if not stack or stack['status'] != 'active':
            return await inter.edit_original_response(embed=err_embed("Стак не найден или неактивен."))

        if not stack['recruitment_open']:
            return await inter.edit_original_response(embed=err_embed(
                f"Набор в стак **{stack['stack_name']}** закрыт. "
                "Приём новых заявок остановлен лидером."
            ))

        if stack['leader_id'] == inter.author.id:
            return await inter.edit_original_response(embed=err_embed("Вы и так лидер этого стака."))

        already = await pool.fetchrow(
            """
            SELECT s.stack_name FROM stack_members m
            JOIN stacks s ON s.stack_id = m.stack_id
            WHERE m.user_id = $1 AND s.status = 'active'
            """,
            inter.author.id
        )
        if already:
            return await inter.edit_original_response(embed=err_embed(f"Вы уже в стаке **{already['stack_name']}**."))

        is_reviewer_somewhere = await pool.fetchval(
            "SELECT 1 FROM stack_reviewers WHERE user_id = $1",
            inter.author.id
        )
        if is_reviewer_somewhere:
            return await inter.edit_original_response(embed=err_embed("Вы являетесь проверяющим в другом стаке."))

        existing_app = await pool.fetchrow(
            "SELECT id FROM stack_applications WHERE applicant_id = $1 AND stack_id = $2 AND status = 'pending'",
            inter.author.id, stack_id
        )
        if existing_app:
            return await inter.edit_original_response(embed=err_embed(f"У вас уже есть активная заявка в **{stack['stack_name']}**."))

        app_id = await pool.fetchval(
            "INSERT INTO stack_applications (stack_id, applicant_id, status, applied_at) VALUES ($1, $2, 'pending', $3) RETURNING id",
            stack_id, inter.author.id, int(time.time())
        )

        recipients = [stack['leader_id']]
        reviewer_rows = await pool.fetch(
            "SELECT user_id FROM stack_reviewers WHERE stack_id = $1",
            stack_id
        )
        for r in reviewer_rows:
            if r['user_id'] not in recipients:
                recipients.append(r['user_id'])

        embed = disnake.Embed(title="—・Новая заявка в стак", color=COLOR_WARN)
        embed.add_field(name="> Стак:", value=f"\u200b**・** **{stack['stack_name']}**", inline=True)
        embed.add_field(name="> От:", value=f"\u200b**・** {inter.author.mention} (`{inter.author.id}`)", inline=True)
        embed.add_field(name="> ID заявки:", value=f"\u200b**・** `{app_id}`", inline=False)
        if len(recipients) > 1:
            embed.set_footer(text=f"Заявка отправлена {len(recipients)} получателям. Решение примет первый ответивший.")

        sent_count = 0
        failed_recipients = []
        first_msg_id = 0
        for rid in recipients:
            try:
                user = inter.guild.get_member(rid) or await self.bot.fetch_user(rid)
                dm_msg = await user.send(embed=embed, view=LeaderReviewView())
                await pool.execute(
                    """
                    INSERT INTO stack_application_messages (application_id, recipient_id, channel_id, message_id)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (application_id, recipient_id) DO UPDATE SET channel_id = $3, message_id = $4
                    """,
                    app_id, rid, dm_msg.channel.id, dm_msg.id
                )
                if rid == stack['leader_id']:
                    first_msg_id = dm_msg.id
                sent_count += 1
            except disnake.Forbidden:
                failed_recipients.append(rid)
            except Exception:
                failed_recipients.append(rid)

        if first_msg_id:
            await pool.execute("UPDATE stack_applications SET leader_msg_id = $1 WHERE id = $2", first_msg_id, app_id)

        if sent_count == 0:
            await pool.execute("DELETE FROM stack_applications WHERE id = $1", app_id)
            return await inter.edit_original_response(embed=err_embed(
                "Не удалось доставить заявку никому из проверяющих (у всех закрыты ЛС). Попробуйте позже."
            ))

        msg_text = f"Заявка в **{stack['stack_name']}** отправлена ({sent_count} получателям)."
        if failed_recipients:
            msg_text += f"\n⚠️ Не удалось доставить: {len(failed_recipients)}."
        await inter.edit_original_response(embed=ok_embed(msg_text))

    async def _handle_leader_review(self, inter, cid):
        passed, retry = await self.bot.check_cooldown_redis(inter.author.id, "app_review", 2.0)
        if not passed:
            if not inter.response.is_done():
                await inter.response.send_message(
                    embed=disnake.Embed(title="⏳ Анти-Спам", description=f"Ждите {retry} сек.", color=COLOR_WARN),
                    ephemeral=True
                )
            return

        await inter.response.defer(ephemeral=True, with_message=True)

        pool = self.bot.pool

        app = await pool.fetchrow(
            """
            SELECT a.id, a.stack_id, a.applicant_id, a.status,
                   s.leader_id, s.stack_name, s.role_id, s.guild_id
            FROM stack_application_messages m
            JOIN stack_applications a ON a.id = m.application_id
            JOIN stacks s ON s.stack_id = a.stack_id
            WHERE m.message_id = $1 AND m.recipient_id = $2
            """,
            inter.message.id, inter.author.id
        )
        if not app:
            app = await pool.fetchrow(
                """
                SELECT a.id, a.stack_id, a.applicant_id, a.status,
                       s.leader_id, s.stack_name, s.role_id, s.guild_id
                FROM stack_applications a
                JOIN stacks s ON s.stack_id = a.stack_id
                WHERE a.leader_msg_id = $1
                """,
                inter.message.id
            )
        if not app:
            return await inter.edit_original_response(embed=err_embed("Заявка не найдена."))

        is_leader = app['leader_id'] == inter.author.id
        is_reviewer = False
        if not is_leader:
            is_reviewer = bool(await pool.fetchval(
                "SELECT 1 FROM stack_reviewers WHERE stack_id = $1 AND user_id = $2",
                app['stack_id'], inter.author.id
            ))
        if not (is_leader or is_reviewer):
            return await inter.edit_original_response(embed=err_embed(
                "Только лидер или проверяющий могут обрабатывать заявку."
            ))

        if app['status'] != 'pending':
            return await inter.edit_original_response(embed=err_embed("Эта заявка уже обработана."))

        now = int(time.time())
        reviewer_role_label = "лидером" if is_leader else "проверяющим"

        if cid == "app_accept":
            already = await pool.fetchrow(
                """
                SELECT s.stack_name FROM stack_members m
                JOIN stacks s ON s.stack_id = m.stack_id
                WHERE m.user_id = $1 AND s.status = 'active'
                """,
                app['applicant_id']
            )
            if already:
                await pool.execute(
                    "UPDATE stack_applications SET status = 'rejected', processed_at = $1, processed_by = $2, rejection_reason = $3 WHERE id = $4",
                    now, inter.author.id, "applicant_already_in_stack", app['id']
                )
                await self._update_all_application_messages(
                    app['id'], inter.author.id,
                    result_text=f"❌ Заявитель уже в стаке **{already['stack_name']}**.",
                    color=COLOR_ERR
                )
                return await inter.edit_original_response(
                    embed=err_embed(f"Заявитель уже состоит в стаке **{already['stack_name']}**.")
                )

            guild = self.bot.get_guild(app['guild_id'])
            if not guild:
                return await inter.edit_original_response(embed=err_embed("Сервер не найден."))
            applicant = guild.get_member(app['applicant_id'])
            role = guild.get_role(app['role_id'])
            if applicant and role:
                try:
                    await applicant.add_roles(role, reason=f"Принят в стак {app['stack_name']}")
                except Exception:
                    pass

            await pool.execute(
                "INSERT INTO stack_members (stack_id, user_id, joined_at, added_by) VALUES ($1, $2, $3, $4) ON CONFLICT DO NOTHING",
                app['stack_id'], app['applicant_id'], now, inter.author.id
            )
            await pool.execute(
                "UPDATE stack_applications SET status = 'accepted', processed_at = $1, processed_by = $2 WHERE id = $3",
                now, inter.author.id, app['id']
            )

            await self._update_all_application_messages(
                app['id'], inter.author.id,
                result_text=f"✅ Принято {reviewer_role_label} <@{inter.author.id}> в **{app['stack_name']}**",
                color=COLOR_OK
            )

            try:
                if applicant:
                    await applicant.send(embed=ok_embed(f"Ваша заявка в стак **{app['stack_name']}** принята!"))
            except Exception:
                pass

            cfg = await load_config()
            log_ch = guild.get_channel(cfg.settings.stacks.approval_log_channel_id)
            if log_ch:
                try:
                    await log_ch.send(
                        f"➕ <@{app['applicant_id']}> вступил в стак **{app['stack_name']}** "
                        f"(одобрил {reviewer_role_label} <@{inter.author.id}>)."
                    )
                except Exception:
                    pass

            await inter.edit_original_response(embed=ok_embed(f"Вы приняли заявку в стак **{app['stack_name']}**."))

        else:
            await pool.execute(
                "UPDATE stack_applications SET status = 'rejected', processed_at = $1, processed_by = $2 WHERE id = $3",
                now, inter.author.id, app['id']
            )
            await self._update_all_application_messages(
                app['id'], inter.author.id,
                result_text=f"❌ Отклонено {reviewer_role_label} <@{inter.author.id}>",
                color=COLOR_ERR
            )

            try:
                applicant = await self.bot.fetch_user(app['applicant_id'])
                await applicant.send(embed=err_embed(f"Ваша заявка в стак **{app['stack_name']}** отклонена."))
            except Exception:
                pass

            await inter.edit_original_response(embed=ok_embed(f"Заявка в стак **{app['stack_name']}** отклонена."))

    async def _update_all_application_messages(self, application_id: int, decider_id: int, result_text: str, color: int):
        pool = self.bot.pool
        rows = await pool.fetch(
            "SELECT recipient_id, channel_id, message_id FROM stack_application_messages WHERE application_id = $1",
            application_id
        )
        for r in rows:
            try:
                channel = self.bot.get_channel(r['channel_id']) or await self.bot.fetch_channel(r['channel_id'])
                msg = await channel.fetch_message(r['message_id'])
                if not msg.embeds:
                    continue
                emb = msg.embeds[0]
                emb.color = color
                if r['recipient_id'] == decider_id:
                    emb.add_field(name="> Результат:", value=f"\u200b**・** {result_text} (вы)", inline=False)
                else:
                    emb.add_field(
                        name="> Результат:",
                        value=f"\u200b**・** {result_text}",
                        inline=False
                    )
                await msg.edit(embed=emb, view=None)
            except Exception:
                pass


def setup(bot):
    bot.add_cog(Stacks(bot))
