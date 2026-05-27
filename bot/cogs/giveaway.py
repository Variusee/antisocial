import disnake
from disnake.ext import commands, tasks
import json
import time
import random
import sys
from typing import Optional
from disnake import TextInputStyle

sys.path.insert(0, "/root/antisocial")

from shared import schema
from shared.config_manager import load_config
from shared.staff import is_staff
from shared.error_log import send_error_log


async def _check_user_has_tag(bot, user_id: int, live: bool = False) -> bool:
    try:
        from bot.cogs.clan_tag import db_is_wearing, db_check_live
        if live:
            return await db_check_live(bot, user_id)
        db_result = await db_is_wearing(bot, user_id)
        if not db_result:
            return await db_check_live(bot, user_id)
        return db_result
    except Exception:
        return False


async def _resolve_target_names(bot, pool, msg_id: str, guild_id: int) -> list[str]:
    rows = await pool.fetch(
        "SELECT target_id FROM giveaway_voice_targets WHERE message_id = $1",
        msg_id
    )
    if not rows:
        return []
    guild = bot.get_guild(int(guild_id)) if guild_id else None
    names = []
    for r in rows:
        ch = guild.get_channel(r['target_id']) if guild else None
        names.append(ch.name if ch else f"({r['target_id']})")
    return names


GRACE_CHECK_INTERVAL = 30


async def _redis_invite_counter_key(guild_id: int):
    return f"gw:invcache:{guild_id}"


async def _redis_code_map_key():
    return "gw:codemap"


async def _redis_mass_join_key(msg_id: str):
    return f"gw:massjoin:{msg_id}"


def parse_time(t_str: str) -> Optional[int]:
    mult = {"m": 60, "h": 3600, "d": 86400}
    try:
        return int(t_str[:-1]) * mult[t_str[-1]]
    except Exception:
        return None


async def has_dangerous_flags(member: disnake.Member) -> bool:
    try:
        flags = member.public_flags
        if flags is None:
            return False
        bad_names = ["spammer", "quarantined", "disabled_suspicious_activity"]
        for name in bad_names:
            if hasattr(flags, name) and getattr(flags, name):
                return True
    except Exception:
        pass
    return False


async def validate_invite_join(pool, guild_id: int, inviter_id: int,
                                member: disnake.Member, gw_start_time: float,
                                ac_config) -> tuple[bool, Optional[str], int]:
    now = int(time.time())
    score = 0

    if member.bot:
        return False, schema.REJECT_BOT, 100

    if member.id == inviter_id:
        return False, schema.REJECT_SELF, 100

    if await has_dangerous_flags(member):
        if ac_config.check_suspicious_flags:
            return False, schema.REJECT_SUSPICIOUS_FLAGS, 100
        else:
            score += 50

    created_ts = int(member.created_at.timestamp())
    age_seconds = now - created_ts
    min_age = ac_config.min_account_age_days * 86400

    if age_seconds < min_age:
        return False, schema.REJECT_YOUNG_ACCOUNT, 100

    if gw_start_time > 0 and created_ts > gw_start_time:
        return False, schema.REJECT_ACCOUNT_CREATED_AFTER_GW, 100

    young_threshold = ac_config.young_threshold_days * 86400
    if ac_config.require_avatar_if_young and age_seconds < young_threshold:
        if member.avatar is None:
            return False, schema.REJECT_NO_AVATAR, 100

    existed = await pool.fetchval(
        "SELECT 1 FROM historical_joins WHERE guild_id = $1 AND user_id = $2",
        guild_id, member.id
    )
    if existed:
        return False, schema.REJECT_OLD_MEMBER, 100

    dup = await pool.fetchrow(
        """
        SELECT inviter_id FROM gw_invite_joins
        WHERE joined_user_id = $1
          AND status IN ('valid', 'pending')
          AND message_id IN (SELECT message_id FROM giveaways WHERE ended = FALSE AND guild_id = $2)
        LIMIT 1
        """,
        member.id, guild_id
    )
    if dup and dup['inviter_id'] != inviter_id:
        return False, schema.REJECT_DUPLICATE_INVITER, 100

    return True, None, score


async def build_giveaway_embed(prize, winners, end_time, entries_count, ended=False, winners_list=None, gw_type=None, target_id=None, target_names=None):
    embed = disnake.Embed(title=f"—・Розыгрыш — {prize}", color=0x2b2d31)
    desc = f"• Количество победителей: {winners}\n"
    if gw_type == "booster":
        desc += "• Условие: только активные бустеры сервера\n"
    elif gw_type == "clan_tag":
        desc += "• Условие: носители тега **ANTI**\n"
    elif gw_type == "voice":
        if target_id == -1:
            desc += "• Условие: в любой категории сервера (войс)\n"
        elif target_id == -2:
            desc += "• Условие: в любом войс-канале сервера\n"
        elif target_id == 0 and target_names:
            joined = ", ".join(target_names)
            desc += f"• Условие: в одной из категорий: {joined}\n"
        elif target_id and target_id > 0:
            desc += f"• Условие: в канале/категории <#{target_id}>\n"
    elif gw_type == "invite":
        desc += "• Условие: приглашения через личную ссылку\n"
    if ended:
        desc += f"• Завершено: <t:{int(end_time)}:R> ( <t:{int(end_time)}:F> )\n\nУчастников — {entries_count}"
        if winners_list:
            desc += f"\nПобедители: {', '.join(f'<@{w}>' for w in winners_list)}"
        else:
            desc += "\nПобедители: Нет победителей"
    else:
        desc += f"• Заканчивается: <t:{int(end_time)}:R> ( <t:{int(end_time)}:F> )\n\nУчастников — {entries_count}"
    embed.description = desc
    return embed


async def pick_winners(pool, msg_id, gw_type, n_winners, participants, bot=None):
    if not participants:
        return [], []
    disqualified = []
    if gw_type == "clan_tag" and bot is not None:
        valid_participants = []
        for uid in participants:
            try:
                still_wearing = await _check_user_has_tag(bot, uid, live=True)
            except Exception:
                still_wearing = False
            if still_wearing:
                valid_participants.append(uid)
            else:
                disqualified.append(uid)
        participants = valid_participants
        if not participants:
            return [], disqualified
    if gw_type == "invite":
        rows = await pool.fetch(
            """
            SELECT inviter_id, COUNT(*) AS cnt
            FROM gw_invite_joins
            WHERE message_id = $1 AND status = 'valid'
            GROUP BY inviter_id
            ORDER BY cnt DESC
            """,
            msg_id
        )
        valid = [r['inviter_id'] for r in rows if r['cnt'] > 0]
        if not valid:
            return [], disqualified
        winners = [valid[0]]
        remaining = valid[1:]
        extra = min(n_winners - 1, len(remaining))
        if extra > 0:
            winners.extend(random.sample(remaining, extra))
        return winners, disqualified
    if len(participants) <= n_winners:
        return list(participants), disqualified
    return random.sample(participants, n_winners), disqualified


class EntriesPaginationView(disnake.ui.View):
    def __init__(self, lines):
        super().__init__(timeout=300)
        self.lines = lines
        self.current_page = 0
        self.per_page = 25
        self.max_pages = max(0, (len(self.lines) - 1) // self.per_page)
        self._update_buttons()

    def _update_buttons(self):
        self.prev_btn.disabled = self.current_page == 0
        self.next_btn.disabled = self.current_page == self.max_pages

    def get_embed(self):
        start = self.current_page * self.per_page
        end = start + self.per_page
        page_lines = self.lines[start:end]
        embed = disnake.Embed(title="—・Участники розыгрыша", description="\n".join(page_lines) or "Пусто", color=0x2b2d31)
        embed.set_footer(text=f"Страница {self.current_page + 1} из {self.max_pages + 1} | Всего: {len(self.lines)}")
        return embed

    @disnake.ui.button(label="◀ Пред.", style=disnake.ButtonStyle.secondary)
    async def prev_btn(self, button, inter):
        self.current_page -= 1
        self._update_buttons()
        await inter.response.edit_message(embed=self.get_embed(), view=self)

    @disnake.ui.button(label="След. ▶", style=disnake.ButtonStyle.secondary)
    async def next_btn(self, button, inter):
        self.current_page += 1
        self._update_buttons()
        await inter.response.edit_message(embed=self.get_embed(), view=self)


async def send_entries_list(inter: disnake.MessageInteraction):
    await inter.response.defer(ephemeral=True, with_message=True)
    msg_id = str(inter.message.id)
    pool = inter.bot.pool
    gw = await pool.fetchrow("SELECT type FROM giveaways WHERE message_id = $1", msg_id)
    if not gw:
        return await inter.edit_original_response(content="Розыгрыш не найден.")
    participants_rows = await pool.fetch("SELECT user_id FROM gw_participants WHERE message_id = $1", msg_id)
    participants = [r['user_id'] for r in participants_rows]
    if not participants:
        return await inter.edit_original_response(embed=disnake.Embed(title="—・Участники розыгрыша", description="Пока нет участников.", color=0x2b2d31))
    lines = []
    if gw['type'] == "invite":
        counts = await pool.fetch(
            """
            SELECT p.user_id,
                   COALESCE(SUM(CASE WHEN j.status = 'valid' THEN 1 ELSE 0 END), 0) AS valid
            FROM gw_participants p
            LEFT JOIN gw_invite_joins j ON j.message_id = p.message_id AND j.inviter_id = p.user_id
            WHERE p.message_id = $1
            GROUP BY p.user_id
            ORDER BY valid DESC
            """,
            msg_id
        )
        for i, r in enumerate(counts):
            n = r['valid']
            word = "инвайт" if n % 10 == 1 and n % 100 != 11 else "инвайта" if 2 <= n % 10 <= 4 and (n % 100 < 10 or n % 100 >= 20) else "инвайтов"
            lines.append(f"{i+1}) <@{r['user_id']}> — **{n}** {word}")
    else:
        for i, uid in enumerate(participants):
            lines.append(f"{i+1}) <@{uid}>")
    if len(lines) <= 25:
        embed = disnake.Embed(title="—・Участники розыгрыша", description="\n".join(lines), color=0x2b2d31)
        embed.set_footer(text=f"Всего: {len(lines)}")
        await inter.edit_original_response(embed=embed)
    else:
        view = EntriesPaginationView(lines)
        await inter.edit_original_response(embed=view.get_embed(), view=view)


class GiveawayJoinView(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def interaction_check(self, inter):
        passed, retry = await inter.bot.check_cooldown_redis(inter.author.id, "gw_join", 1.5)
        if not passed:
            if not inter.response.is_done():
                await inter.response.send_message(embed=disnake.Embed(title="⏳ Анти-Спам", description=f"Ждите {retry} сек.", color=0xF8E3A1), ephemeral=True)
            return False
        return True

    @disnake.ui.button(label="Участвовать", style=disnake.ButtonStyle.primary, custom_id="gw_join")
    async def join_btn(self, button, inter):
        await inter.response.defer(ephemeral=True, with_message=True)
        msg_id = str(inter.message.id)
        uid = inter.author.id
        pool = inter.bot.pool
        gw = await pool.fetchrow("SELECT prize, winners, end_time, type, target_id, ended FROM giveaways WHERE message_id = $1", msg_id)
        if not gw:
            return await inter.edit_original_response(content="Розыгрыш не найден.")
        if gw['ended']:
            return await inter.edit_original_response(content="Розыгрыш уже завершен.")
        is_participant = await pool.fetchval("SELECT 1 FROM gw_participants WHERE message_id = $1 AND user_id = $2", msg_id, uid)
        if gw['type'] == "booster" and not inter.author.premium_since:
            return await inter.edit_original_response(content="Этот розыгрыш только для бустеров сервера.")
        if gw['type'] == "voice":
            vc = inter.author.voice.channel if inter.author.voice else None
            if not vc:
                return await inter.edit_original_response(content="Вы не в нужном голосовом канале/категории.")
            target_id = gw['target_id']
            ok = False
            if target_id == -2:
                ok = isinstance(vc, (disnake.VoiceChannel, disnake.StageChannel))
            elif target_id == -1:
                ok = bool(vc.category_id)
            elif target_id == 0:
                rows = await pool.fetch("SELECT target_id FROM giveaway_voice_targets WHERE message_id = $1", msg_id)
                allowed = {r['target_id'] for r in rows}
                ok = (vc.id in allowed) or (vc.category_id in allowed)
            else:
                ok = (vc.id == target_id) or (vc.category_id == target_id)
            if not ok:
                return await inter.edit_original_response(content="Вы не в нужном голосовом канале/категории.")
        if gw['type'] == "clan_tag":
            has_tag = await _check_user_has_tag(inter.bot, uid, live=False)
            if not has_tag:
                has_tag_live = await _check_user_has_tag(inter.bot, uid, live=True)
                if not has_tag_live:
                    return await inter.edit_original_response(content="❌ Этот розыгрыш только для носителей тега **ANTI**. Поставьте тег на профиль и попробуйте снова.")

        if gw['type'] == "invite":
            if is_participant:
                inv_row = await pool.fetchrow("SELECT code FROM gw_invites WHERE message_id = $1 AND inviter_id = $2", msg_id, uid)
                if not inv_row:
                    await pool.execute("DELETE FROM gw_participants WHERE message_id = $1 AND user_id = $2", msg_id, uid)
                    return await inter.edit_original_response(content="Ошибка инвайта, попробуйте снова.")
                valid_cnt = await pool.fetchval("SELECT COUNT(*) FROM gw_invite_joins WHERE message_id = $1 AND inviter_id = $2 AND status = 'valid'", msg_id, uid) or 0
                pending_cnt = await pool.fetchval("SELECT COUNT(*) FROM gw_invite_joins WHERE message_id = $1 AND inviter_id = $2 AND status = 'pending'", msg_id, uid) or 0
                cancel_view = disnake.ui.View(timeout=300)
                cancel_view.add_item(disnake.ui.Button(label="Отменить участие", style=disnake.ButtonStyle.danger, custom_id=f"gw_cancel_{msg_id}"))
                return await inter.edit_original_response(content=f"Ваша ссылка: https://discord.gg/{inv_row['code']}\n✅ Засчитано: **{valid_cnt}** | ⏳ Ожидают проверки: **{pending_cnt}**", view=cancel_view)
            try:
                invite = await inter.channel.create_invite(max_age=0, max_uses=0, unique=True, reason="Giveaway")
            except Exception:
                return await inter.edit_original_response(content="Ошибка создания инвайта. Проверьте права бота.")
            async with pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute("INSERT INTO gw_invites (message_id, inviter_id, code) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING", msg_id, uid, invite.code)
                    await conn.execute("INSERT INTO gw_participants (message_id, user_id) VALUES ($1, $2) ON CONFLICT DO NOTHING", msg_id, uid)
            try:
                await inter.bot.redis.hset("gw:codemap", invite.code, f"{msg_id}:{uid}")
            except Exception:
                pass
            cog = inter.bot.get_cog("Giveaways")
            if cog:
                cog.pending_updates.add(msg_id)
            return await inter.edit_original_response(content=f"Вы присоединились!\nВаша ссылка: https://discord.gg/{invite.code}")

        if is_participant:
            await pool.execute("DELETE FROM gw_participants WHERE message_id = $1 AND user_id = $2", msg_id, uid)
            msg = "Вы покинули розыгрыш."
        else:
            await pool.execute("INSERT INTO gw_participants (message_id, user_id) VALUES ($1, $2) ON CONFLICT DO NOTHING", msg_id, uid)
            msg = "Вы присоединились."
        cog = inter.bot.get_cog("Giveaways")
        if cog:
            cog.pending_updates.add(msg_id)
        await inter.edit_original_response(content=msg)

    @disnake.ui.button(label="Участники", style=disnake.ButtonStyle.secondary, custom_id="gw_entries")
    async def entries_btn(self, button, inter):
        await send_entries_list(inter)


class GiveawayEndedView(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @disnake.ui.button(label="Участники", style=disnake.ButtonStyle.secondary, custom_id="gw_entries_ended")
    async def entries_btn(self, button, inter):
        await send_entries_list(inter)


class GiveawayCreateModal(disnake.ui.Modal):
    def __init__(self, gw_type, target_id=0):
        self.gw_type = gw_type
        self.target_id = target_id
        self.multi_targets = []
        components = [
            disnake.ui.TextInput(label="Приз", placeholder="Например: VIP статус", custom_id="prize", style=TextInputStyle.short, max_length=100),
            disnake.ui.TextInput(label="Количество победителей", placeholder="Например: 1", custom_id="winners", style=TextInputStyle.short, max_length=2),
            disnake.ui.TextInput(label="Время (1m, 30m, 2h, 1d)", placeholder="Например: 30m", custom_id="duration", style=TextInputStyle.short, max_length=10)
        ]
        super().__init__(title="Создание розыгрыша", components=components)

    async def callback(self, inter):
        await inter.response.defer(ephemeral=True, with_message=True)
        try:
            winners = int(inter.text_values["winners"])
            if winners < 1:
                raise ValueError
        except Exception:
            return await inter.edit_original_response(content="Неверное количество победителей.")
        duration = parse_time(inter.text_values["duration"])
        if not duration:
            return await inter.edit_original_response(content="Неверный формат времени.")
        if self.gw_type == "voice" and duration > 10800:
            return await inter.edit_original_response(content="Для голосового розыгрыша максимум — 3 часа.")
        if duration > 30 * 86400:
            return await inter.edit_original_response(content="Максимум — 30 дней.")
        start_time = time.time()
        end_time = start_time + duration
        prize = inter.text_values["prize"]
        target_names = []
        if self.gw_type == "voice" and self.target_id == 0 and self.multi_targets:
            guild = inter.guild
            for t_id in self.multi_targets:
                ch = guild.get_channel(t_id) if guild else None
                target_names.append(ch.name if ch else f"({t_id})")
        embed = await build_giveaway_embed(
            prize, winners, end_time, 0,
            gw_type=self.gw_type, target_id=self.target_id, target_names=target_names
        )
        msg = await inter.channel.send(embed=embed, view=GiveawayJoinView())
        await inter.edit_original_response(content="Розыгрыш запущен!")
        await inter.bot.pool.execute(
            """INSERT INTO giveaways (message_id, prize, winners, end_time, start_time, type, target_id, channel_id, guild_id, ended)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, FALSE)""",
            str(msg.id), prize, winners, end_time, start_time, self.gw_type, self.target_id, msg.channel.id, msg.guild.id
        )
        if self.gw_type == "voice" and self.target_id == 0 and self.multi_targets:
            for t_id in self.multi_targets:
                await inter.bot.pool.execute(
                    "INSERT INTO giveaway_voice_targets (message_id, target_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                    str(msg.id), t_id
                )


class GiveawayVoiceModeSelectView(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @disnake.ui.string_select(placeholder="Куда нужно зайти?", options=[
        disnake.SelectOption(label="Один канал/категория", value="one", description="Выбрать конкретный канал или категорию"),
        disnake.SelectOption(label="Несколько категорий", value="multi", description="До 25 категорий — присутствие в любой засчитывается"),
        disnake.SelectOption(label="Все категории сервера", value="all_cats", description="Все категории, в которых есть войс-каналы"),
        disnake.SelectOption(label="Все войс-каналы сервера", value="all_voice", description="Любой войс-канал сервера"),
    ])
    async def select_mode(self, select, inter):
        mode = select.values[0]
        if mode == "one":
            await inter.response.edit_message(content="Выберите канал/категорию:", view=GiveawayChannelSelectView())
        elif mode == "multi":
            await inter.response.edit_message(content="Выберите до 25 категорий:", view=GiveawayMultiCategorySelectView())
        elif mode == "all_cats":
            await inter.response.send_modal(GiveawayCreateModal("voice", -1))
        elif mode == "all_voice":
            await inter.response.send_modal(GiveawayCreateModal("voice", -2))


class GiveawayChannelSelectView(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @disnake.ui.channel_select(placeholder="Выберите канал/категорию", channel_types=[disnake.ChannelType.voice, disnake.ChannelType.category], min_values=1, max_values=1)
    async def select_channel(self, select, inter):
        await inter.response.send_modal(GiveawayCreateModal("voice", select.values[0].id))


class GiveawayMultiCategorySelectView(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @disnake.ui.channel_select(placeholder="Выберите до 25 категорий", channel_types=[disnake.ChannelType.category], min_values=1, max_values=25)
    async def select_categories(self, select, inter):
        ids = [c.id for c in select.values]
        modal = GiveawayCreateModal("voice", 0)
        modal.multi_targets = ids
        await inter.response.send_modal(modal)


class GiveawayTypeSelectView(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @disnake.ui.string_select(placeholder="Выберите тип розыгрыша", options=[
        disnake.SelectOption(label="Обычный", value="normal", description="Без условий"),
        disnake.SelectOption(label="Для бустеров", value="booster", description="Только для активных бустеров"),
        disnake.SelectOption(label="Голосовой", value="voice", description="Требует присутствия в канале"),
        disnake.SelectOption(label="Инвайты", value="invite", description="Побеждает тот, кто пригласит больше"),
        disnake.SelectOption(label="Клан-тег", value="clan_tag", description="Только для носителей нашего тега (ANTI)"),
    ])
    async def select_type(self, select, inter):
        if select.values[0] == "voice":
            await inter.response.edit_message(content="Где должен быть участник?", view=GiveawayVoiceModeSelectView())
        else:
            await inter.response.send_modal(GiveawayCreateModal(select.values[0]))


class GiveawayActionView(disnake.ui.View):
    def __init__(self, bot, gw_data, parent_view):
        super().__init__(timeout=300)
        self.bot = bot
        self.gw_data = gw_data
        self.msg_id = str(gw_data['message_id'])
        self.parent_view = parent_view
        self.is_ended = bool(gw_data['ended'])
        self._build_components()

    def _build_components(self):
        self.clear_items()

        if self.gw_data['type'] == "invite":
            user_select = disnake.ui.UserSelect(placeholder="Детальный отчёт по участнику", min_values=1, max_values=1, row=0)
            user_select.callback = self._inviter_detail_cb
            self.add_item(user_select)

        row = 1
        if self.is_ended:
            reroll = disnake.ui.Button(label="Реролл", style=disnake.ButtonStyle.success, emoji="🎲", row=row)
            reroll.callback = self._reroll_cb
            self.add_item(reroll)

            stats = disnake.ui.Button(label="Статистика", style=disnake.ButtonStyle.secondary, emoji="📊", row=row)
            stats.callback = self._stats_cb
            self.add_item(stats)

            delete = disnake.ui.Button(label="Удалить", style=disnake.ButtonStyle.danger, emoji="🗑️", row=row)
            delete.callback = self._delete_cb
            self.add_item(delete)
        else:
            end = disnake.ui.Button(label="Итоги (досрочно)", style=disnake.ButtonStyle.success, emoji="🎉", row=row)
            end.callback = self._force_end_cb
            self.add_item(end)

            resend = disnake.ui.Button(label="Переотправить", style=disnake.ButtonStyle.primary, emoji="🔄", row=row)
            resend.callback = self._resend_cb
            self.add_item(resend)

            stats = disnake.ui.Button(label="Статистика", style=disnake.ButtonStyle.secondary, emoji="📊", row=row)
            stats.callback = self._stats_cb
            self.add_item(stats)

            delete = disnake.ui.Button(label="Удалить", style=disnake.ButtonStyle.danger, emoji="🗑️", row=row)
            delete.callback = self._delete_cb
            self.add_item(delete)

        back = disnake.ui.Button(label="Назад к списку", style=disnake.ButtonStyle.grey, emoji="↩️", row=2)
        back.callback = self._back_cb
        self.add_item(back)

    def _build_embed(self):
        status_text = "Завершён" if self.is_ended else "Активен"
        embed = disnake.Embed(title="—・Управление розыгрышем", color=0xF6C4C5 if self.is_ended else 0x2b2d31)
        embed.add_field(name="> Приз:", value=f"\u200b**・** {self.gw_data['prize']}", inline=False)
        embed.add_field(name="> Статус:", value=f"\u200b**・** {status_text}", inline=True)
        embed.add_field(name="> Тип:", value=f"\u200b**・** `{self.gw_data['type']}`", inline=True)
        embed.add_field(name="> ID:", value=f"\u200b**・** `{self.gw_data['message_id']}`", inline=True)
        embed.add_field(name="> Канал:", value=f"\u200b**・** <#{self.gw_data['channel_id']}>", inline=True)
        embed.add_field(name="> Окончание:", value=f"\u200b**・** <t:{int(self.gw_data['end_time'])}:R>", inline=True)
        return embed

    async def rerender_action(self, inter):
        gw = await self.bot.pool.fetchrow("SELECT * FROM giveaways WHERE message_id = $1", self.msg_id)
        if gw:
            self.gw_data = dict(gw)
            self.is_ended = bool(gw['ended'])
            self._build_components()
        try:
            await inter.edit_original_response(embed=self._build_embed(), view=self)
        except Exception:
            try:
                await inter.response.edit_message(embed=self._build_embed(), view=self)
            except Exception:
                pass

    async def _inviter_detail_cb(self, inter):
        raw_value = inter.data.values[0]
        inviter_id = int(raw_value)
        is_participant = await self.bot.pool.fetchval(
            "SELECT 1 FROM gw_invites WHERE message_id = $1 AND inviter_id = $2",
            self.msg_id, inviter_id
        )
        if not is_participant:
            return await inter.response.send_message(f"<@{inviter_id}> не участвует в этом розыгрыше через инвайты.", ephemeral=True)
        await inter.response.defer()
        detail_view = InviterDetailView(self.bot, self.gw_data, self, inviter_id)
        embed = await detail_view.build()
        try:
            await inter.edit_original_response(embed=embed, view=detail_view)
        except Exception:
            pass

    async def _finalize(self, inter, is_reroll=False):
        pool = self.bot.pool
        gw = await pool.fetchrow("SELECT prize, winners, end_time, type, target_id, channel_id, guild_id FROM giveaways WHERE message_id = $1", self.msg_id)
        if not gw:
            return await inter.edit_original_response(content="Розыгрыш не найден.", view=None, embed=None)
        p_rows = await pool.fetch("SELECT user_id FROM gw_participants WHERE message_id = $1", self.msg_id)
        participants = [r['user_id'] for r in p_rows]
        winners_list, disqualified = await pick_winners(pool, self.msg_id, gw['type'], gw['winners'], participants, bot=self.bot)
        await pool.execute("UPDATE giveaways SET ended = TRUE, winners_list = $1 WHERE message_id = $2", json.dumps(winners_list), self.msg_id)
        try:
            ch = self.bot.get_channel(gw['channel_id']) or await self.bot.fetch_channel(gw['channel_id'])
            msg = await ch.fetch_message(int(self.msg_id))
            target_names = await _resolve_target_names(self.bot, pool, self.msg_id, gw['guild_id']) if gw['type'] == 'voice' and gw['target_id'] == 0 else []
            embed = await build_giveaway_embed(
                gw['prize'], gw['winners'], gw['end_time'], len(participants),
                ended=True, winners_list=winners_list,
                gw_type=gw['type'], target_id=gw['target_id'], target_names=target_names
            )
            await msg.edit(embed=embed, view=GiveawayEndedView())
            prefix = "🎲 **Реролл!** " if is_reroll else ""
            extra_note = ""
            if gw['type'] == "clan_tag" and disqualified:
                extra_note = f"\n\n_Дисквалифицировано (сняли тег): {len(disqualified)}_"
            if winners_list:
                await ch.send(f"{prefix}Поздравляем {', '.join(f'<@{w}>' for w in winners_list)}! Вы выиграли **{gw['prize']}**!\nhttps://discord.com/channels/{inter.guild.id}/{gw['channel_id']}/{self.msg_id}{extra_note}")
            else:
                await ch.send(f"{prefix}Розыгрыш на **{gw['prize']}** завершён. Никто не выполнил условия.{extra_note}")
            await inter.edit_original_response(content="✅ Готово.", view=None, embed=None)
        except Exception as e:
            await inter.edit_original_response(content=f"❌ Ошибка: {e}", view=None, embed=None)

    async def _force_end_cb(self, inter):
        if self.is_ended:
            return await inter.response.send_message("Уже завершён.", ephemeral=True)
        await inter.response.defer(ephemeral=True)
        await self._finalize(inter)

    async def _reroll_cb(self, inter):
        pool = self.bot.pool
        row = await pool.fetchrow("SELECT winners_list, type FROM giveaways WHERE message_id = $1", self.msg_id)
        if not row or not row['winners_list']:
            return await inter.response.send_message(embed=disnake.Embed(title="—・Ошибка", description="У этого розыгрыша нет сохранённых победителей.", color=0xF6C4C5), ephemeral=True)
        try:
            winners = json.loads(row['winners_list'])
        except Exception:
            winners = []
        if not winners:
            return await inter.response.send_message(embed=disnake.Embed(title="—・Ошибка", description="Список победителей пуст.", color=0xF6C4C5), ephemeral=True)

        if len(winners) == 1:
            await inter.response.defer(ephemeral=True)
            await self._do_reroll(inter, winners, winner_to_replace=winners[0])
            return

        view = RerollTargetView(self.bot, self.msg_id, winners, self.gw_data)
        embed = disnake.Embed(title="—・Выбор победителя для реролла", description="Кого хотите перебросить? Можно выбрать одного или **Всех**.", color=0x2b2d31)
        await inter.response.send_message(embed=embed, view=view, ephemeral=True)

    async def _do_reroll(self, inter, current_winners, winner_to_replace=None):
        pool = self.bot.pool
        gw_type = self.gw_data['type']

        if gw_type == "invite":
            rows = await pool.fetch(
                """
                SELECT inviter_id, COUNT(*) AS c
                FROM gw_invite_joins
                WHERE message_id = $1 AND status = 'valid'
                GROUP BY inviter_id
                ORDER BY c DESC
                """,
                self.msg_id
            )
            pool_candidates = [r['inviter_id'] for r in rows]
        else:
            rows = await pool.fetch("SELECT user_id FROM gw_participants WHERE message_id = $1", self.msg_id)
            pool_candidates = [r['user_id'] for r in rows]

        existing = set(current_winners)
        if winner_to_replace is None:
            excluded = existing
            count_needed = len(current_winners)
        else:
            excluded = existing - {winner_to_replace}
            count_needed = 1

        candidates = [u for u in pool_candidates if u not in excluded]
        if not candidates:
            return await inter.followup.send(embed=disnake.Embed(title="—・Ошибка", description="Нет кандидатов для реролла (все уже были победителями или пул пуст).", color=0xF6C4C5), ephemeral=True)

        new_winners_picked = random.sample(candidates, min(count_needed, len(candidates)))

        if winner_to_replace is None:
            new_winners = new_winners_picked
        else:
            new_winners = [w if w != winner_to_replace else new_winners_picked[0] for w in current_winners]

        await pool.execute("UPDATE giveaways SET winners_list = $1 WHERE message_id = $2", json.dumps(new_winners), self.msg_id)

        channel = inter.channel
        mentions = ", ".join(f"<@{w}>" for w in new_winners)
        if winner_to_replace is not None:
            announce_text = f"🔄 Реролл! <@{winner_to_replace}> заменён: **{mentions}**\nПриз: **{self.gw_data['prize']}**"
        else:
            announce_text = f"🔄 Полный реролл! Новые победители: **{mentions}**\nПриз: **{self.gw_data['prize']}**"
        try:
            await channel.send(announce_text)
        except Exception:
            pass

        await inter.followup.send(embed=disnake.Embed(title="—・Успешно", description="Реролл выполнен.", color=0x9EE5B4), ephemeral=True)

    async def _stats_cb(self, inter):
        await inter.response.defer(ephemeral=True)
        pool = self.bot.pool
        p_count = await pool.fetchval("SELECT COUNT(*) FROM gw_participants WHERE message_id = $1", self.msg_id) or 0
        embed = disnake.Embed(title="—・Статистика розыгрыша", color=0x2b2d31)
        embed.add_field(name="> Приз:", value=self.gw_data['prize'], inline=False)
        embed.add_field(name="> Тип:", value=f"`{self.gw_data['type']}`", inline=True)
        embed.add_field(name="> Участников:", value=str(p_count), inline=True)

        has_details = False
        if self.gw_data['type'] == "invite":
            valid = await pool.fetchval("SELECT COUNT(*) FROM gw_invite_joins WHERE message_id = $1 AND status = 'valid'", self.msg_id) or 0
            pending = await pool.fetchval("SELECT COUNT(*) FROM gw_invite_joins WHERE message_id = $1 AND status = 'pending'", self.msg_id) or 0
            invalid = await pool.fetchval("SELECT COUNT(*) FROM gw_invite_joins WHERE message_id = $1 AND status = 'invalid'", self.msg_id) or 0
            total = valid + pending + invalid
            has_details = total > 0

            embed.add_field(name="> ✅ Валидных:", value=str(valid), inline=True)
            embed.add_field(name="> ⏳ Ожидают:", value=str(pending), inline=True)
            embed.add_field(name="> ❌ Отклонённых:", value=str(invalid), inline=True)

            top_inviters = await pool.fetch(
                """
                SELECT inviter_id, COUNT(*) AS c
                FROM gw_invite_joins
                WHERE message_id = $1 AND status = 'valid'
                GROUP BY inviter_id
                ORDER BY c DESC
                LIMIT 5
                """,
                self.msg_id
            )
            if top_inviters:
                top_lines = []
                for i, r in enumerate(top_inviters):
                    medal = ["🥇", "🥈", "🥉", "4.", "5."][i]
                    top_lines.append(f"{medal} <@{r['inviter_id']}> — **{r['c']}**")
                embed.add_field(name="> 🏆 Топ приглашающих:", value="\n".join(top_lines), inline=False)

            reasons = await pool.fetch(
                """
                SELECT rejection_reason, COUNT(*) AS c
                FROM gw_invite_joins
                WHERE message_id = $1 AND status = 'invalid' AND rejection_reason IS NOT NULL
                GROUP BY rejection_reason
                ORDER BY c DESC
                """,
                self.msg_id
            )
            if reasons:
                txt = "\n".join([f"\u200b**・** `{r['rejection_reason']}`: **{r['c']}**" for r in reasons])
                embed.add_field(name="> Причины отклонений:", value=txt, inline=False)

        if self.is_ended:
            row = await pool.fetchrow("SELECT winners_list FROM giveaways WHERE message_id = $1", self.msg_id)
            if row and row['winners_list']:
                try:
                    wl = json.loads(row['winners_list'])
                    if wl:
                        embed.add_field(name="> 🏆 Победители:", value=", ".join(f"<@{w}>" for w in wl), inline=False)
                except Exception:
                    pass

        view = None
        if has_details:
            view = disnake.ui.View(timeout=300)
            details_btn = disnake.ui.Button(label="Подробности", style=disnake.ButtonStyle.primary, emoji="📋")

            async def _open_details(i):
                details_view = GiveawayStatsDetailsView(self.bot, self.msg_id, self.gw_data['type'])
                details_embed = await details_view.build()
                await i.response.edit_message(embed=details_embed, view=details_view)

            details_btn.callback = _open_details
            view.add_item(details_btn)

        await inter.edit_original_response(embed=embed, view=view)

    async def _resend_cb(self, inter):
        if self.is_ended:
            return await inter.response.send_message("Нельзя переотправить завершённый.", ephemeral=True)
        await inter.response.defer(ephemeral=True)
        pool = self.bot.pool
        gw = await pool.fetchrow("SELECT prize, winners, end_time, type, target_id, channel_id, guild_id FROM giveaways WHERE message_id = $1", self.msg_id)
        if not gw:
            return await inter.edit_original_response(content="Не найден.", view=None, embed=None)
        p_count = await pool.fetchval("SELECT COUNT(*) FROM gw_participants WHERE message_id = $1", self.msg_id) or 0
        target_names = await _resolve_target_names(self.bot, pool, self.msg_id, gw['guild_id']) if gw['type'] == 'voice' and gw['target_id'] == 0 else []
        embed = await build_giveaway_embed(
            gw['prize'], gw['winners'], gw['end_time'], p_count,
            gw_type=gw['type'], target_id=gw['target_id'], target_names=target_names
        )
        try:
            msg = await inter.channel.send(embed=embed, view=GiveawayJoinView())
        except Exception as e:
            return await inter.edit_original_response(content=f"❌ Ошибка: {e}", view=None, embed=None)
        try:
            old_ch = self.bot.get_channel(gw['channel_id']) or await self.bot.fetch_channel(gw['channel_id'])
            old_msg = await old_ch.fetch_message(int(self.msg_id))
            await old_msg.delete()
        except Exception:
            pass
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("UPDATE giveaways SET message_id = $1, channel_id = $2 WHERE message_id = $3", str(msg.id), inter.channel.id, self.msg_id)
                await conn.execute("UPDATE gw_participants SET message_id = $1 WHERE message_id = $2", str(msg.id), self.msg_id)
                await conn.execute("UPDATE gw_invites SET message_id = $1 WHERE message_id = $2", str(msg.id), self.msg_id)
                await conn.execute("UPDATE gw_invite_joins SET message_id = $1 WHERE message_id = $2", str(msg.id), self.msg_id)
        await inter.edit_original_response(content=f"✅ Переотправлен. Новый ID: `{msg.id}`", embed=None, view=None)

    async def _delete_cb(self, inter):
        await inter.response.defer(ephemeral=True)
        pool = self.bot.pool
        gw = await pool.fetchrow("SELECT channel_id FROM giveaways WHERE message_id = $1", self.msg_id)
        if not gw:
            return await inter.edit_original_response(content="Не найден.", view=None, embed=None)
        codes = await pool.fetch("SELECT code FROM gw_invites WHERE message_id = $1", self.msg_id)
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("DELETE FROM gw_invite_joins WHERE message_id = $1", self.msg_id)
                await conn.execute("DELETE FROM gw_invites WHERE message_id = $1", self.msg_id)
                await conn.execute("DELETE FROM gw_participants WHERE message_id = $1", self.msg_id)
                await conn.execute("DELETE FROM giveaway_voice_targets WHERE message_id = $1", self.msg_id)
                await conn.execute("DELETE FROM giveaways WHERE message_id = $1", self.msg_id)
        try:
            if codes and self.bot.redis:
                await self.bot.redis.hdel("gw:codemap", *[r['code'] for r in codes])
        except Exception:
            pass
        try:
            ch = self.bot.get_channel(gw['channel_id']) or await self.bot.fetch_channel(gw['channel_id'])
            msg = await ch.fetch_message(int(self.msg_id))
            await msg.delete()
        except Exception:
            pass
        await inter.edit_original_response(content=f"✅ Розыгрыш `{self.msg_id}` удалён.", view=None, embed=None)

    async def _back_cb(self, inter):
        await inter.response.defer()
        try:
            await self.parent_view.refresh_view(inter.guild.id)
        except Exception:
            pass
        try:
            await inter.edit_original_response(embed=self.parent_view.get_embed(), view=self.parent_view)
        except Exception:
            pass


class GiveawayManageMainView(disnake.ui.View):
    def __init__(self, bot, giveaways, author_id):
        super().__init__(timeout=300)
        self.bot = bot
        self.all_gws = giveaways
        self.author_id = author_id
        self.page = 0
        self.per_page = 25
        self.filter = "all"
        self._rebuild()

    async def interaction_check(self, inter):
        if inter.author.id != self.author_id:
            await inter.response.send_message(embed=disnake.Embed(title="—・Ошибка", description="Эта панель не для вас.", color=0xF6C4C5), ephemeral=True)
            return False
        return True

    def _filtered(self):
        if self.filter == "active":
            return [g for g in self.all_gws if not g['ended']]
        elif self.filter == "ended":
            return [g for g in self.all_gws if g['ended']]
        return self.all_gws

    def _rebuild(self):
        self.clear_items()
        filtered = self._filtered()
        total_pages = max(1, (len(filtered) - 1) // self.per_page + 1)
        if self.page >= total_pages:
            self.page = total_pages - 1
        if self.page < 0:
            self.page = 0
        start = self.page * self.per_page
        end = start + self.per_page
        page_gws = filtered[start:end]

        if page_gws:
            opts = []
            for g in page_gws:
                icon = "🔴" if g['ended'] else "🟢"
                if g['type'] == "invite":
                    type_icon = "🎟️"
                elif g['type'] == "clan_tag":
                    type_icon = "🏷️"
                elif g['type'] == "voice":
                    type_icon = "🎤"
                elif g['type'] == "booster":
                    type_icon = "💎"
                else:
                    type_icon = "🎁"
                prize_short = (g['prize'][:50] + "…") if len(g['prize']) > 50 else g['prize']
                label = f"{icon} {type_icon} {prize_short}"
                desc = f"ID: {g['message_id']}"
                opts.append(disnake.SelectOption(label=label[:100], description=desc[:100], value=str(g['message_id'])))

            sel = disnake.ui.StringSelect(placeholder="Выбрать розыгрыш", options=opts, row=0)
            sel.callback = self._on_select
            self.add_item(sel)

        prev_b = disnake.ui.Button(label="◀", style=disnake.ButtonStyle.secondary, row=1, disabled=self.page == 0)
        prev_b.callback = self._prev
        self.add_item(prev_b)

        page_b = disnake.ui.Button(label=f"Стр. {self.page+1}/{total_pages}", style=disnake.ButtonStyle.grey, disabled=True, row=1)
        self.add_item(page_b)

        next_b = disnake.ui.Button(label="▶", style=disnake.ButtonStyle.secondary, row=1, disabled=self.page >= total_pages - 1)
        next_b.callback = self._next
        self.add_item(next_b)

        filter_select = disnake.ui.StringSelect(
            placeholder=f"Фильтр: {self._filter_label()}",
            options=[
                disnake.SelectOption(label="Все", value="all", emoji="📋", default=self.filter == "all"),
                disnake.SelectOption(label="🟢 Активные", value="active", default=self.filter == "active"),
                disnake.SelectOption(label="🔴 Завершённые", value="ended", default=self.filter == "ended"),
            ],
            row=2
        )
        filter_select.callback = self._filter_cb
        self.add_item(filter_select)

    def _filter_label(self):
        return {"all": "Все", "active": "Активные", "ended": "Завершённые"}.get(self.filter, "Все")

    def get_embed(self):
        filtered = self._filtered()
        total = len(filtered)
        total_pages = max(1, (total - 1) // self.per_page + 1)
        desc = f"**Всего розыгрышей:** {len(self.all_gws)}\n**Фильтр:** {self._filter_label()} → **{total}**\n\nВыберите розыгрыш из списка."
        embed = disnake.Embed(title=f"—・Управление | Стр. {self.page+1}/{total_pages}", description=desc, color=0x2b2d31)
        return embed

    async def refresh_view(self, guild_id):
        self.all_gws = await self.bot.pool.fetch(
            "SELECT * FROM giveaways WHERE guild_id = $1 ORDER BY ended ASC, end_time ASC",
            guild_id
        )
        self._rebuild()

    async def _on_select(self, inter):
        msg_id = inter.data.values[0]
        gw = next((g for g in self.all_gws if str(g['message_id']) == msg_id), None)
        if not gw:
            return await inter.response.send_message(embed=disnake.Embed(title="—・Ошибка", description="Розыгрыш не найден.", color=0xF6C4C5), ephemeral=True)
        view = GiveawayActionView(self.bot, dict(gw), parent_view=self)
        embed = view._build_embed()
        await inter.response.edit_message(embed=embed, view=view)

    async def _prev(self, inter):
        self.page -= 1
        self._rebuild()
        await inter.response.edit_message(embed=self.get_embed(), view=self)

    async def _next(self, inter):
        self.page += 1
        self._rebuild()
        await inter.response.edit_message(embed=self.get_embed(), view=self)

    async def _filter_cb(self, inter):
        self.filter = inter.data.values[0]
        self.page = 0
        self._rebuild()
        await inter.response.edit_message(embed=self.get_embed(), view=self)


class InviterDetailView(disnake.ui.View):
    PER_PAGE = 10

    def __init__(self, bot, gw_data, parent_view, inviter_id):
        super().__init__(timeout=300)
        self.bot = bot
        self.gw_data = gw_data
        self.msg_id = str(gw_data['message_id'])
        self.parent_view = parent_view
        self.inviter_id = inviter_id
        self.page = 0
        self.status_filter = None
        self._cached_rows = None

    async def _fetch_rows(self, force=False):
        if self._cached_rows is not None and not force:
            return self._cached_rows
        if self.status_filter:
            rows = await self.bot.pool.fetch(
                """
                SELECT id, joined_user_id, status, rejection_reason, joined_at,
                       manually_overridden, overridden_by
                FROM gw_invite_joins
                WHERE message_id = $1 AND inviter_id = $2 AND status = $3
                ORDER BY joined_at DESC
                """,
                self.msg_id, self.inviter_id, self.status_filter
            )
        else:
            rows = await self.bot.pool.fetch(
                """
                SELECT id, joined_user_id, status, rejection_reason, joined_at,
                       manually_overridden, overridden_by
                FROM gw_invite_joins
                WHERE message_id = $1 AND inviter_id = $2
                ORDER BY
                    CASE status WHEN 'valid' THEN 0 WHEN 'pending' THEN 1 ELSE 2 END,
                    joined_at DESC
                """,
                self.msg_id, self.inviter_id
            )
        self._cached_rows = rows
        return rows

    async def build(self):
        rows = await self._fetch_rows()
        total = len(rows)
        total_pages = max(1, (total - 1) // self.PER_PAGE + 1)
        if self.page >= total_pages:
            self.page = total_pages - 1
        if self.page < 0:
            self.page = 0
        start = self.page * self.PER_PAGE
        page_rows = rows[start:start + self.PER_PAGE]

        filter_label = {
            None: "Все",
            "valid": "✅ Валидные",
            "pending": "⏳ Ожидают",
            "invalid": "❌ Отклонённые",
        }.get(self.status_filter, "Все")

        embed = disnake.Embed(
            title=f"—・Приглашения от <@{self.inviter_id}>",
            description=f"Фильтр: **{filter_label}**\nЗаписей: **{total}**\nСтраница: **{self.page + 1}/{total_pages}**",
            color=0x2b2d31
        )

        if not page_rows:
            embed.add_field(name="> Записей нет", value="Под этот фильтр ничего не попало.", inline=False)
        else:
            for r in page_rows:
                icon = {"valid": "✅", "pending": "⏳", "invalid": "❌"}.get(r['status'], "?")
                status_text = {"valid": "Валидный", "pending": "Ожидает", "invalid": "Отклонён"}.get(r['status'], "?")
                suffix = ""
                if r['manually_overridden']:
                    suffix = f" 🛠️(<@{r['overridden_by']}>)"
                value = f"Статус: {icon} **{status_text}**{suffix}\nКогда: <t:{r['joined_at']}:R>"
                if r['status'] == 'invalid' and r['rejection_reason']:
                    value += f"\nПричина: `{r['rejection_reason']}`"
                embed.add_field(name=f"→ <@{r['joined_user_id']}>", value=value, inline=False)

        self.clear_items()

        if page_rows:
            entry_opts = []
            for r in page_rows:
                icon = {"valid": "✅", "pending": "⏳", "invalid": "❌"}.get(r['status'], "?")
                entry_opts.append(disnake.SelectOption(
                    label=f"{icon} Запись #{r['id']}",
                    description=f"User: {r['joined_user_id']}",
                    value=str(r['id'])
                ))
            entry_sel = disnake.ui.StringSelect(placeholder="Изменить статус записи", options=entry_opts[:25], row=0)
            entry_sel.callback = self._on_entry_select
            self.add_item(entry_sel)

        prev_b = disnake.ui.Button(label="◀", style=disnake.ButtonStyle.secondary, row=1, disabled=self.page == 0)
        prev_b.callback = self._prev
        self.add_item(prev_b)

        page_btn = disnake.ui.Button(label=f"Стр. {self.page + 1}/{total_pages}", style=disnake.ButtonStyle.grey, disabled=True, row=1)
        self.add_item(page_btn)

        next_b = disnake.ui.Button(label="▶", style=disnake.ButtonStyle.secondary, row=1, disabled=self.page >= total_pages - 1)
        next_b.callback = self._next
        self.add_item(next_b)

        filter_sel = disnake.ui.StringSelect(
            placeholder=f"Фильтр: {filter_label}",
            options=[
                disnake.SelectOption(label="Все", value="all", emoji="📋", default=self.status_filter is None),
                disnake.SelectOption(label="Валидные", value="valid", emoji="✅", default=self.status_filter == "valid"),
                disnake.SelectOption(label="Ожидают", value="pending", emoji="⏳", default=self.status_filter == "pending"),
                disnake.SelectOption(label="Отклонённые", value="invalid", emoji="❌", default=self.status_filter == "invalid"),
            ],
            row=2
        )
        filter_sel.callback = self._filter_cb
        self.add_item(filter_sel)

        back_b = disnake.ui.Button(label="Назад", style=disnake.ButtonStyle.grey, emoji="↩️", row=3)
        back_b.callback = self._back
        self.add_item(back_b)

        return embed

    async def _on_entry_select(self, inter):
        entry_id = int(inter.data.values[0])
        row = next((r for r in (self._cached_rows or []) if r['id'] == entry_id), None)
        if not row:
            return await inter.response.send_message(embed=disnake.Embed(title="—・Ошибка", description="Запись не найдена.", color=0xF6C4C5), ephemeral=True)
        view = JoinEntryActionView(self.bot, self.msg_id, self.inviter_id, entry_id, row, self.gw_data, parent=self)
        embed = disnake.Embed(
            title=f"—・Действия с записью #{entry_id}",
            description=f"Приглашённый: <@{row['joined_user_id']}>\nТекущий статус: **{row['status']}**\nКогда: <t:{row['joined_at']}:F>",
            color=0x2b2d31
        )
        await inter.response.edit_message(embed=embed, view=view)

    async def _prev(self, inter):
        self.page -= 1
        embed = await self.build()
        await inter.response.edit_message(embed=embed, view=self)

    async def _next(self, inter):
        self.page += 1
        embed = await self.build()
        await inter.response.edit_message(embed=embed, view=self)

    async def _filter_cb(self, inter):
        val = inter.data.values[0]
        self.status_filter = None if val == "all" else val
        self.page = 0
        self._cached_rows = None
        embed = await self.build()
        await inter.response.edit_message(embed=embed, view=self)

    async def _back(self, inter):
        try:
            await inter.response.defer()
        except Exception:
            pass

        try:
            await self.parent_view.rerender_action(inter)
            return
        except Exception:
            pass

        try:
            await inter.edit_original_response(
                embed=disnake.Embed(title="—・Ошибка возврата", description="Не удалось вернуться в меню. Откройте /управление_розыгрышами заново.", color=0xF6C4C5),
                view=None
            )
        except Exception:
            pass


class JoinEntryActionView(disnake.ui.View):
    def __init__(self, bot, msg_id, inviter_id, entry_id, row, gw_data, parent):
        super().__init__(timeout=180)
        self.bot = bot
        self.msg_id = msg_id
        self.inviter_id = inviter_id
        self.entry_id = entry_id
        self.row = row
        self.gw_data = gw_data
        self.parent = parent

    @disnake.ui.button(label="✅ Разрешить (valid)", style=disnake.ButtonStyle.success, row=0)
    async def make_valid(self, button, inter):
        await self._change_status(inter, "valid", "manually_allowed_by_admin")

    @disnake.ui.button(label="❌ Удалить (invalid)", style=disnake.ButtonStyle.danger, row=0)
    async def make_invalid(self, button, inter):
        await self._change_status(inter, "invalid", "manually_rejected_by_admin")

    @disnake.ui.button(label="Назад", style=disnake.ButtonStyle.grey, emoji="↩️", row=1)
    async def back(self, button, inter):
        try:
            self.parent._cached_rows = None
            embed = await self.parent.build()
            await inter.response.edit_message(embed=embed, view=self.parent)
        except Exception:
            try:
                if not inter.response.is_done():
                    await inter.response.defer()
                await inter.edit_original_response(
                    embed=disnake.Embed(title="—・Ошибка возврата", description="Не удалось обновить меню.", color=0xF6C4C5),
                    view=None
                )
            except Exception:
                pass

    async def _change_status(self, inter, new_status, reason):
        await self.bot.pool.execute(
            """
            UPDATE gw_invite_joins
            SET status = $1, rejection_reason = $2, manually_overridden = TRUE,
                overridden_by = $3, overridden_at = $4
            WHERE id = $5
            """,
            new_status, reason if new_status == "invalid" else None, inter.author.id, int(time.time()), self.entry_id
        )
        self.parent._cached_rows = None
        embed = await self.parent.build()
        await inter.response.edit_message(embed=embed, view=self.parent)
        try:
            await inter.followup.send(
                embed=disnake.Embed(title="—・Успешно", description=f"Статус записи #{self.entry_id} изменён на **{new_status}**.", color=0x9EE5B4),
                ephemeral=True
            )
        except Exception:
            pass


class GiveawayStatsDetailsView(disnake.ui.View):
    PER_PAGE = 15

    def __init__(self, bot, msg_id, gw_type):
        super().__init__(timeout=300)
        self.bot = bot
        self.msg_id = msg_id
        self.gw_type = gw_type
        self.page = 0
        self._status_filter = None
        self._cached_rows = None

    async def _load_data(self):
        if self._cached_rows is not None:
            return self._cached_rows
        filt = self._status_filter
        if self.gw_type == "invite":
            if filt:
                rows = await self.bot.pool.fetch(
                    """
                    SELECT inviter_id, joined_user_id, status, rejection_reason,
                           joined_at, manually_overridden, overridden_by
                    FROM gw_invite_joins
                    WHERE message_id = $1 AND status = $2
                    ORDER BY joined_at DESC
                    """,
                    self.msg_id, filt
                )
            else:
                rows = await self.bot.pool.fetch(
                    """
                    SELECT inviter_id, joined_user_id, status, rejection_reason,
                           joined_at, manually_overridden, overridden_by
                    FROM gw_invite_joins
                    WHERE message_id = $1
                    ORDER BY
                        CASE status WHEN 'valid' THEN 0 WHEN 'pending' THEN 1 ELSE 2 END,
                        joined_at DESC
                    """,
                    self.msg_id
                )
        else:
            rows = []
        self._cached_rows = rows
        return rows

    async def build(self):
        rows = await self._load_data()
        total = len(rows)
        total_pages = max(1, (total - 1) // self.PER_PAGE + 1)
        if self.page >= total_pages:
            self.page = total_pages - 1
        if self.page < 0:
            self.page = 0
        start = self.page * self.PER_PAGE
        end = start + self.PER_PAGE
        page_rows = rows[start:end]

        embed = disnake.Embed(
            title="—・Подробности инвайтов",
            description=f"Страница **{self.page+1}/{total_pages}** | Всего записей: **{total}**",
            color=0x2b2d31
        )

        if not page_rows:
            embed.add_field(name="> Записей нет", value="Под этот фильтр ничего не попало.", inline=False)
        else:
            for r in page_rows:
                icon = {"valid": "✅", "pending": "⏳", "invalid": "❌"}.get(r['status'], "?")
                status_text = {"valid": "Принят", "pending": "Ожидает", "invalid": "Отклонён"}.get(r['status'], "?")
                override_suffix = ""
                if r['manually_overridden']:
                    override_suffix = f" 🛠️ (админом <@{r['overridden_by']}>)"
                reason_part = ""
                if r['status'] == 'invalid' and r['rejection_reason']:
                    reason_part = f"\nПричина: `{r['rejection_reason']}`"
                value = (
                    f"Пригласил: <@{r['inviter_id']}>\n"
                    f"Статус: {icon} **{status_text}**{override_suffix}\n"
                    f"Когда: <t:{r['joined_at']}:R>"
                    f"{reason_part}"
                )
                embed.add_field(name=f"> Приглашённый: <@{r['joined_user_id']}>", value=value, inline=False)

        embed.set_footer(text="🛠️ — статус изменён админом вручную")

        self.clear_items()

        if total_pages > 1:
            prev_b = disnake.ui.Button(label="◀ Пред.", style=disnake.ButtonStyle.secondary, row=0, disabled=self.page == 0)
            prev_b.callback = self._prev
            self.add_item(prev_b)

            page_b = disnake.ui.Button(label=f"Стр. {self.page+1}/{total_pages}", style=disnake.ButtonStyle.grey, disabled=True, row=0)
            self.add_item(page_b)

            next_b = disnake.ui.Button(label="След. ▶", style=disnake.ButtonStyle.secondary, row=0, disabled=self.page >= total_pages - 1)
            next_b.callback = self._next
            self.add_item(next_b)

        filter_select = disnake.ui.StringSelect(
            placeholder="Фильтр по статусу",
            options=[
                disnake.SelectOption(label="Все", value="all", emoji="📋", default=self._status_filter is None),
                disnake.SelectOption(label="Только валидные", value="valid", emoji="✅", default=self._status_filter == "valid"),
                disnake.SelectOption(label="Только ожидающие", value="pending", emoji="⏳", default=self._status_filter == "pending"),
                disnake.SelectOption(label="Только отклонённые", value="invalid", emoji="❌", default=self._status_filter == "invalid"),
            ],
            row=1
        )
        filter_select.callback = self._filter_cb
        self.add_item(filter_select)

        return embed

    async def _prev(self, inter):
        self.page -= 1
        self._cached_rows = None
        embed = await self.build()
        await inter.response.edit_message(embed=embed, view=self)

    async def _next(self, inter):
        self.page += 1
        self._cached_rows = None
        embed = await self.build()
        await inter.response.edit_message(embed=embed, view=self)

    async def _filter_cb(self, inter):
        val = inter.data.values[0]
        self._status_filter = None if val == "all" else val
        self.page = 0
        self._cached_rows = None
        embed = await self.build()
        await inter.response.edit_message(embed=embed, view=self)


class RerollTargetView(disnake.ui.View):
    def __init__(self, bot, msg_id, winners, gw_data):
        super().__init__(timeout=180)
        self.bot = bot
        self.msg_id = msg_id
        self.winners = winners
        self.gw_data = gw_data
        self.page = 0
        self.per_page = 24
        self._rebuild()

    def _rebuild(self):
        self.clear_items()
        total = len(self.winners)
        total_pages = max(1, (total - 1) // self.per_page + 1)
        if self.page >= total_pages:
            self.page = total_pages - 1
        start = self.page * self.per_page
        end = start + self.per_page
        page_winners = self.winners[start:end]

        if page_winners:
            options = []
            for i, w in enumerate(page_winners, start=start + 1):
                options.append(disnake.SelectOption(
                    label=f"Победитель #{i}",
                    description=f"ID: {w}",
                    value=str(w),
                    emoji="🏆"
                ))
            select = disnake.ui.StringSelect(placeholder="Выбрать одного победителя для реролла", options=options, min_values=1, max_values=1, row=0)
            select.callback = self._on_select
            self.add_item(select)

        if total_pages > 1:
            prev_b = disnake.ui.Button(label="◀", style=disnake.ButtonStyle.secondary, row=1, disabled=self.page == 0)
            prev_b.callback = self._prev
            self.add_item(prev_b)

            page_b = disnake.ui.Button(label=f"Стр. {self.page+1}/{total_pages}", style=disnake.ButtonStyle.grey, disabled=True, row=1)
            self.add_item(page_b)

            next_b = disnake.ui.Button(label="▶", style=disnake.ButtonStyle.secondary, row=1, disabled=self.page >= total_pages - 1)
            next_b.callback = self._next
            self.add_item(next_b)

        all_btn = disnake.ui.Button(label=f"Перебросить всех ({len(self.winners)})", style=disnake.ButtonStyle.danger, emoji="🎲", row=2)
        all_btn.callback = self._on_all
        self.add_item(all_btn)

        cancel_btn = disnake.ui.Button(label="Отмена", style=disnake.ButtonStyle.grey, row=2)
        cancel_btn.callback = self._on_cancel
        self.add_item(cancel_btn)

    def get_embed(self):
        total = len(self.winners)
        total_pages = max(1, (total - 1) // self.per_page + 1)
        embed = disnake.Embed(
            title=f"—・Выбор победителя для реролла | Стр. {self.page+1}/{total_pages}",
            description=f"**Всего победителей:** {total}\n\nВыберите одного в меню ниже или нажмите «Перебросить всех».",
            color=0x2b2d31
        )
        return embed

    async def _on_select(self, inter):
        await inter.response.defer(ephemeral=True)
        target_id = int(inter.data.values[0])
        parent = self
        class Helper:
            def __init__(self, bot, msg_id, gw_data):
                self.bot = bot
                self.msg_id = msg_id
                self.gw_data = gw_data
            async def _do_reroll(self, inter, winners, winner_to_replace=None):
                pool = self.bot.pool
                gw_type = self.gw_data['type']

                if gw_type == "invite":
                    rows = await pool.fetch(
                        "SELECT inviter_id, COUNT(*) AS c FROM gw_invite_joins WHERE message_id = $1 AND status = 'valid' GROUP BY inviter_id ORDER BY c DESC",
                        self.msg_id
                    )
                    pool_candidates = [r['inviter_id'] for r in rows]
                else:
                    rows = await pool.fetch("SELECT user_id FROM gw_participants WHERE message_id = $1", self.msg_id)
                    pool_candidates = [r['user_id'] for r in rows]

                existing = set(winners)
                if winner_to_replace is None:
                    excluded = existing
                    count_needed = len(winners)
                else:
                    excluded = existing - {winner_to_replace}
                    count_needed = 1

                candidates = [u for u in pool_candidates if u not in excluded]
                if not candidates:
                    return await inter.followup.send(embed=disnake.Embed(title="—・Ошибка", description="Нет кандидатов для реролла.", color=0xF6C4C5), ephemeral=True)

                new_winners_picked = random.sample(candidates, min(count_needed, len(candidates)))

                if winner_to_replace is None:
                    new_winners = new_winners_picked
                else:
                    new_winners = [w if w != winner_to_replace else new_winners_picked[0] for w in winners]

                await pool.execute("UPDATE giveaways SET winners_list = $1 WHERE message_id = $2", json.dumps(new_winners), self.msg_id)

                channel = inter.channel
                mentions = ", ".join(f"<@{w}>" for w in new_winners)
                if winner_to_replace is not None:
                    announce_text = f"🔄 Реролл! <@{winner_to_replace}> заменён: **{mentions}**\nПриз: **{self.gw_data['prize']}**"
                else:
                    announce_text = f"🔄 Полный реролл! Новые победители: **{mentions}**\nПриз: **{self.gw_data['prize']}**"
                try:
                    await channel.send(announce_text)
                except Exception:
                    pass

                await inter.followup.send(embed=disnake.Embed(title="—・Успешно", description="Реролл выполнен.", color=0x9EE5B4), ephemeral=True)

        helper = Helper(self.bot, self.msg_id, self.gw_data)
        await helper._do_reroll(inter, self.winners, winner_to_replace=target_id)

    async def _on_all(self, inter):
        await inter.response.defer(ephemeral=True)
        parent = self
        class Helper:
            def __init__(self, bot, msg_id, gw_data):
                self.bot = bot
                self.msg_id = msg_id
                self.gw_data = gw_data
            async def _do_reroll(self, inter, winners, winner_to_replace=None):
                pool = self.bot.pool
                gw_type = self.gw_data['type']

                if gw_type == "invite":
                    rows = await pool.fetch(
                        "SELECT inviter_id, COUNT(*) AS c FROM gw_invite_joins WHERE message_id = $1 AND status = 'valid' GROUP BY inviter_id ORDER BY c DESC",
                        self.msg_id
                    )
                    pool_candidates = [r['inviter_id'] for r in rows]
                else:
                    rows = await pool.fetch("SELECT user_id FROM gw_participants WHERE message_id = $1", self.msg_id)
                    pool_candidates = [r['user_id'] for r in rows]

                existing = set(winners)
                if winner_to_replace is None:
                    excluded = existing
                    count_needed = len(winners)
                else:
                    excluded = existing - {winner_to_replace}
                    count_needed = 1

                candidates = [u for u in pool_candidates if u not in excluded]
                if not candidates:
                    return await inter.followup.send(embed=disnake.Embed(title="—・Ошибка", description="Нет кандидатов для реролла.", color=0xF6C4C5), ephemeral=True)

                new_winners_picked = random.sample(candidates, min(count_needed, len(candidates)))

                if winner_to_replace is None:
                    new_winners = new_winners_picked
                else:
                    new_winners = [w if w != winner_to_replace else new_winners_picked[0] for w in winners]

                await pool.execute("UPDATE giveaways SET winners_list = $1 WHERE message_id = $2", json.dumps(new_winners), self.msg_id)

                channel = inter.channel
                mentions = ", ".join(f"<@{w}>" for w in new_winners)
                if winner_to_replace is not None:
                    announce_text = f"🔄 Реролл! <@{winner_to_replace}> заменён: **{mentions}**\nПриз: **{self.gw_data['prize']}**"
                else:
                    announce_text = f"🔄 Полный реролл! Новые победители: **{mentions}**\nПриз: **{self.gw_data['prize']}**"
                try:
                    await channel.send(announce_text)
                except Exception:
                    pass

                await inter.followup.send(embed=disnake.Embed(title="—・Успешно", description="Реролл выполнен.", color=0x9EE5B4), ephemeral=True)

        helper = Helper(self.bot, self.msg_id, self.gw_data)
        await helper._do_reroll(inter, self.winners, winner_to_replace=None)

    async def _on_cancel(self, inter):
        await inter.response.edit_message(embed=disnake.Embed(title="Отменено", color=0x9E9DEA), view=None)

    async def _prev(self, inter):
        self.page -= 1
        self._rebuild()
        await inter.response.edit_message(embed=self.get_embed(), view=self)

    async def _next(self, inter):
        self.page += 1
        self._rebuild()
        await inter.response.edit_message(embed=self.get_embed(), view=self)


class Giveaways(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.pending_updates = set()
        self.giveaway_loop.start()
        self.batch_update_messages.start()
        self.grace_period_loop.start()
        self._startup_done = False

    def cog_unload(self):
        self.giveaway_loop.cancel()
        self.batch_update_messages.cancel()
        self.grace_period_loop.cancel()

    @commands.Cog.listener()
    async def on_clan_tag_changed(self, member, is_wearing: bool):
        if is_wearing:
            return
        try:
            rows = await self.bot.pool.fetch(
                """
                SELECT gw.message_id FROM giveaways gw
                JOIN gw_participants p ON p.message_id = gw.message_id
                WHERE gw.ended = FALSE AND gw.type = 'clan_tag'
                  AND gw.guild_id = $1 AND p.user_id = $2
                """,
                member.guild.id, member.id
            )
            if not rows:
                return
            for r in rows:
                msg_id = r['message_id']
                await self.bot.pool.execute(
                    "DELETE FROM gw_participants WHERE message_id = $1 AND user_id = $2",
                    msg_id, member.id
                )
                self.pending_updates.add(msg_id)
                print(f"[Giveaway] Удалён {member.id} из розыгрыша {msg_id} (снял тег)", flush=True)
        except Exception as e:
            print(f"[Giveaway:on_clan_tag_changed] {type(e).__name__}: {e}", flush=True)

    @commands.Cog.listener()
    async def on_ready(self):
        if self._startup_done:
            return
        self._startup_done = True
        await self.bot.wait_until_ready()
        self.bot.add_view(GiveawayJoinView())
        self.bot.add_view(GiveawayEndedView())
        for guild in self.bot.guilds:
            try:
                invs = await guild.invites()
                mapping = {inv.code: str(inv.uses) for inv in invs}
                if mapping:
                    await self.bot.redis.hset(f"gw:invcache:{guild.id}", mapping=mapping)
            except Exception:
                pass
            try:
                active = await self.bot.pool.fetch(
                    """SELECT i.code, i.message_id, i.inviter_id FROM gw_invites i
                       JOIN giveaways g ON g.message_id = i.message_id
                       WHERE g.ended = FALSE AND g.guild_id = $1""",
                    guild.id
                )
                if active:
                    mapping = {r['code']: f"{r['message_id']}:{r['inviter_id']}" for r in active}
                    await self.bot.redis.hset("gw:codemap", mapping=mapping)
            except Exception:
                pass

    @commands.Cog.listener()
    async def on_interaction(self, inter):
        if inter.type != disnake.InteractionType.component:
            return
        cid = inter.data.custom_id if inter.data else None
        if not cid or not cid.startswith("gw_cancel_"):
            return
        passed, retry = await self.bot.check_cooldown_redis(inter.author.id, "gw_cancel", 1.5)
        if not passed:
            try:
                await inter.response.send_message(embed=disnake.Embed(title="⏳ Анти-Спам", description=f"Ждите {retry} сек.", color=0xF8E3A1), ephemeral=True)
            except Exception:
                pass
            return
        msg_id = cid.replace("gw_cancel_", "")
        uid = inter.author.id
        gw = await self.bot.pool.fetchrow("SELECT ended FROM giveaways WHERE message_id = $1", msg_id)
        if not gw:
            return await inter.response.edit_message(content="Не найден.", view=None)
        if gw['ended']:
            return await inter.response.edit_message(content="Уже завершён.", view=None)
        codes_rows = await self.bot.pool.fetch("SELECT code FROM gw_invites WHERE message_id = $1 AND inviter_id = $2", msg_id, uid)
        async with self.bot.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("DELETE FROM gw_invite_joins WHERE message_id = $1 AND inviter_id = $2", msg_id, uid)
                await conn.execute("DELETE FROM gw_invites WHERE message_id = $1 AND inviter_id = $2", msg_id, uid)
                await conn.execute("DELETE FROM gw_participants WHERE message_id = $1 AND user_id = $2", msg_id, uid)
        try:
            if codes_rows and self.bot.redis:
                await self.bot.redis.hdel("gw:codemap", *[r['code'] for r in codes_rows])
        except Exception:
            pass
        self.pending_updates.add(msg_id)
        await inter.response.edit_message(content="Вы отменили участие.", view=None)

    @commands.Cog.listener()
    async def on_invite_create(self, invite):
        try:
            await self.bot.redis.hset(f"gw:invcache:{invite.guild.id}", invite.code, str(invite.uses))
        except Exception:
            pass

    @commands.Cog.listener()
    async def on_invite_delete(self, invite):
        try:
            await self.bot.redis.hdel(f"gw:invcache:{invite.guild.id}", invite.code)
        except Exception:
            pass

    async def _detect_used_code_via_audit(self, guild: disnake.Guild, member: disnake.Member) -> Optional[str]:
        try:
            cutoff = time.time() - 30
            async for entry in guild.audit_logs(limit=20, action=disnake.AuditLogAction.invite_update):
                if entry.created_at.timestamp() < cutoff:
                    break
                if entry.target and hasattr(entry.target, "code"):
                    return entry.target.code
        except Exception:
            pass
        return None

    @commands.Cog.listener()
    async def on_member_join(self, member: disnake.Member):
        if member.bot:
            return
        guild = member.guild
        pool = self.bot.pool
        now_ts = int(time.time())

        was_historical = await pool.fetchval(
            "SELECT 1 FROM historical_joins WHERE guild_id = $1 AND user_id = $2",
            guild.id, member.id
        )

        used_code = None
        try:
            old_cache = await self.bot.redis.hgetall(f"gw:invcache:{guild.id}") or {}
            new_invs = await guild.invites()
            new_map = {inv.code: inv.uses for inv in new_invs}
            for code, uses in new_map.items():
                if uses > int(old_cache.get(code, 0)):
                    used_code = code
                    break
            mapping = {c: str(u) for c, u in new_map.items()}
            if mapping:
                await self.bot.redis.delete(f"gw:invcache:{guild.id}")
                await self.bot.redis.hset(f"gw:invcache:{guild.id}", mapping=mapping)
        except Exception:
            pass

        if used_code is None:
            used_code = await self._detect_used_code_via_audit(guild, member)

        if used_code:
            code_info = None
            try:
                code_info = await self.bot.redis.hget("gw:codemap", used_code)
            except Exception:
                pass
            if code_info:
                msg_id, inviter_str = code_info.split(":", 1)
                inviter_id = int(inviter_str)
                gw = await pool.fetchrow("SELECT start_time, ended, type FROM giveaways WHERE message_id = $1", msg_id)
                if gw and not gw['ended'] and gw['type'] == "invite":
                    cfg = await load_config()
                    ac_cfg = cfg.giveaway_anticheat
                    is_ok, reason, score = await validate_invite_join(
                        pool, guild.id, inviter_id, member, gw['start_time'] or 0, ac_cfg
                    )
                    if was_historical and is_ok:
                        is_ok = False
                        reason = schema.REJECT_OLD_MEMBER

                    mass_join_triggered = False
                    if is_ok:
                        try:
                            key = f"gw:massjoin:{msg_id}"
                            count = await self.bot.redis.incr(key)
                            if count == 1:
                                await self.bot.redis.expire(key, ac_cfg.mass_join_detection_window)
                            if count >= ac_cfg.mass_join_detection_count:
                                mass_join_triggered = True
                                reason = schema.REJECT_MASS_JOIN
                                is_ok = False
                                score = max(score, 80)
                        except Exception:
                            pass

                    status = schema.STATUS_PENDING if is_ok else schema.STATUS_INVALID
                    await pool.execute(
                        """
                        INSERT INTO gw_invite_joins (message_id, inviter_id, joined_user_id, joined_at, status, rejection_reason, suspicion_score)
                        VALUES ($1, $2, $3, $4, $5, $6, $7)
                        ON CONFLICT (message_id, joined_user_id) DO UPDATE
                        SET inviter_id = $2, status = $5, rejection_reason = $6,
                            manually_overridden = FALSE, overridden_by = 0, overridden_at = 0,
                            suspicion_score = $7
                        """,
                        msg_id, inviter_id, member.id, now_ts, status, reason, score
                    )

                    if mass_join_triggered:
                        await pool.execute(
                            """
                            UPDATE gw_invite_joins SET status = 'invalid', rejection_reason = $1
                            WHERE message_id = $2 AND status = 'pending'
                              AND joined_at >= $3 AND manually_overridden = FALSE
                            """,
                            schema.REJECT_MASS_JOIN, msg_id, now_ts - ac_cfg.mass_join_detection_window
                        )

                    self.pending_updates.add(msg_id)

        if not was_historical:
            await pool.execute(
                "INSERT INTO historical_joins (guild_id, user_id, first_joined_at) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
                guild.id, member.id, now_ts
            )

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        if member.bot:
            return
        pool = self.bot.pool
        cfg = await load_config()
        grace = cfg.giveaway_anticheat.grace_period_seconds
        now_ts = int(time.time())

        affected = await pool.fetch(
            """
            SELECT j.id, j.message_id, j.status, j.joined_at
            FROM gw_invite_joins j
            JOIN giveaways g ON g.message_id = j.message_id
            WHERE j.joined_user_id = $1 AND g.ended = FALSE AND j.manually_overridden = FALSE
              AND j.status IN ('valid', 'pending')
            """,
            member.id
        )
        msg_ids = set()
        for row in affected:
            fast_leave = (now_ts - row['joined_at']) < grace
            reason = schema.REJECT_LEFT_TOO_FAST if fast_leave else schema.REJECT_LEFT
            await pool.execute(
                "UPDATE gw_invite_joins SET status = 'invalid', rejection_reason = $1 WHERE id = $2",
                reason, row['id']
            )
            msg_ids.add(row['message_id'])
        for m in msg_ids:
            self.pending_updates.add(m)

    async def create_giveaway(self, inter):
        if not await is_staff(inter.author.id):
            return await inter.response.send_message(embed=disnake.Embed(title="—・Ошибка", description="Доступ запрещен.", color=0xF6C4C5), ephemeral=True)
        if not inter.response.is_done():
            await inter.response.defer(ephemeral=True, with_message=True)
        await inter.edit_original_response(content="Настройка розыгрыша", view=GiveawayTypeSelectView())

    async def manage_giveaways(self, inter):
        if not await is_staff(inter.author.id):
            return await inter.response.send_message(embed=disnake.Embed(title="—・Ошибка", description="Доступ запрещен.", color=0xF6C4C5), ephemeral=True)
        if not inter.response.is_done():
            await inter.response.defer(ephemeral=True)
        all_gws = await self.bot.pool.fetch(
            "SELECT * FROM giveaways WHERE guild_id = $1 ORDER BY ended ASC, end_time ASC",
            inter.guild.id
        )
        if not all_gws:
            return await inter.edit_original_response(content="Нет розыгрышей для этого сервера.")
        view = GiveawayManageMainView(self.bot, all_gws, inter.author.id)
        await inter.edit_original_response(embed=view.get_embed(), view=view)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if before.channel == after.channel:
            return
        gw_rows = await self.bot.pool.fetch(
            """
            SELECT gw.message_id, gw.target_id, gw.prize
            FROM giveaways gw JOIN gw_participants p ON gw.message_id = p.message_id
            WHERE gw.ended = FALSE AND gw.type = 'voice' AND gw.guild_id = $1 AND p.user_id = $2
            """,
            member.guild.id, member.id
        )
        to_notify = []
        for row in gw_rows:
            target = row['target_id']
            ch = after.channel
            ok = False
            if ch:
                if target == -2:
                    ok = isinstance(ch, (disnake.VoiceChannel, disnake.StageChannel))
                elif target == -1:
                    ok = bool(ch.category_id)
                elif target == 0:
                    rs = await self.bot.pool.fetch("SELECT target_id FROM giveaway_voice_targets WHERE message_id = $1", row['message_id'])
                    allowed = {r['target_id'] for r in rs}
                    ok = (ch.id in allowed) or (ch.category_id in allowed)
                else:
                    ok = (ch.id == target) or (ch.category_id == target)
            if not ok:
                await self.bot.pool.execute("DELETE FROM gw_participants WHERE message_id = $1 AND user_id = $2", row['message_id'], member.id)
                to_notify.append({"msg_id": row['message_id'], "prize": row['prize']})
        for d in to_notify:
            try:
                await member.send(f"Вы покинули голосовой канал и были исключены из розыгрыша на **{d['prize']}**.")
            except Exception:
                pass
            self.pending_updates.add(d['msg_id'])

    @tasks.loop(seconds=5.0)
    async def batch_update_messages(self):
        if not self.pending_updates:
            return
        updates = list(self.pending_updates)
        self.pending_updates.clear()
        for msg_id in updates:
            gw = await self.bot.pool.fetchrow("SELECT prize, winners, end_time, type, target_id, channel_id, guild_id, ended FROM giveaways WHERE message_id = $1", msg_id)
            if not gw or gw['ended']:
                continue
            p_count = await self.bot.pool.fetchval("SELECT COUNT(*) FROM gw_participants WHERE message_id = $1", msg_id) or 0
            try:
                ch = self.bot.get_channel(gw['channel_id']) or await self.bot.fetch_channel(gw['channel_id'])
                msg = await ch.fetch_message(int(msg_id))
                target_names = await _resolve_target_names(self.bot, self.bot.pool, msg_id, gw['guild_id']) if gw['type'] == 'voice' and gw['target_id'] == 0 else []
                embed = await build_giveaway_embed(
                    gw['prize'], gw['winners'], gw['end_time'], p_count,
                    gw_type=gw['type'], target_id=gw['target_id'], target_names=target_names
                )
                await msg.edit(embed=embed)
            except Exception:
                pass

    @batch_update_messages.before_loop
    async def before_batch(self):
        await self.bot.wait_until_ready()

    @tasks.loop(seconds=GRACE_CHECK_INTERVAL)
    async def grace_period_loop(self):
        if not self.bot.pool:
            return
        cfg = await load_config()
        grace = cfg.giveaway_anticheat.grace_period_seconds
        cutoff = int(time.time()) - grace
        result = await self.bot.pool.execute(
            """UPDATE gw_invite_joins SET status = 'valid'
               WHERE status = 'pending' AND joined_at <= $1 AND manually_overridden = FALSE
                 AND message_id IN (SELECT message_id FROM giveaways WHERE ended = FALSE)""",
            cutoff
        )
        try:
            changed = int(result.split()[-1]) if result and " " in result else 0
        except Exception:
            changed = 0
        if changed > 0:
            rows = await self.bot.pool.fetch(
                """SELECT DISTINCT message_id FROM gw_invite_joins
                   WHERE status = 'valid' AND joined_at > $1 - 60""",
                cutoff
            )
            for r in rows:
                self.pending_updates.add(r['message_id'])

    @grace_period_loop.before_loop
    async def before_grace(self):
        await self.bot.wait_until_ready()

    @tasks.loop(seconds=10)
    async def giveaway_loop(self):
        now = time.time()
        if not self.bot.pool:
            return
        ended_gws = await self.bot.pool.fetch(
            "SELECT message_id, prize, winners, type, target_id, channel_id, guild_id, end_time FROM giveaways WHERE ended = FALSE AND end_time <= $1",
            now
        )
        if not ended_gws:
            return
        for gw in ended_gws:
            msg_id = gw['message_id']
            try:
                ch = self.bot.get_channel(gw['channel_id']) or await self.bot.fetch_channel(gw['channel_id'])
            except Exception:
                await self.bot.pool.execute("UPDATE giveaways SET ended = TRUE WHERE message_id = $1", msg_id)
                continue
            p_rows = await self.bot.pool.fetch("SELECT user_id FROM gw_participants WHERE message_id = $1", msg_id)
            participants = [r['user_id'] for r in p_rows]
            winners_list, disqualified = await pick_winners(self.bot.pool, msg_id, gw['type'], gw['winners'], participants, bot=self.bot)
            await self.bot.pool.execute(
                "UPDATE giveaways SET ended = TRUE, winners_list = $1 WHERE message_id = $2",
                json.dumps(winners_list), msg_id
            )
            try:
                msg = await ch.fetch_message(int(msg_id))
                target_names = await _resolve_target_names(self.bot, self.bot.pool, msg_id, gw['guild_id']) if gw['type'] == 'voice' and gw['target_id'] == 0 else []
                embed = await build_giveaway_embed(
                    gw['prize'], gw['winners'], gw['end_time'], len(participants),
                    ended=True, winners_list=winners_list,
                    gw_type=gw['type'], target_id=gw['target_id'], target_names=target_names
                )
                await msg.edit(embed=embed, view=GiveawayEndedView())
            except Exception:
                pass
            try:
                extra_note = ""
                if gw['type'] == "clan_tag" and disqualified:
                    extra_note = f"\n\n_Дисквалифицировано (сняли тег): {len(disqualified)}_"
                if winners_list:
                    await ch.send(f"Поздравляем {', '.join(f'<@{w}>' for w in winners_list)}! Вы выиграли **{gw['prize']}**!\nhttps://discord.com/channels/{ch.guild.id}/{gw['channel_id']}/{msg_id}{extra_note}")
                else:
                    await ch.send(f"Розыгрыш на **{gw['prize']}** завершён. Никто не выполнил условия.{extra_note}")
            except Exception:
                pass

    @giveaway_loop.before_loop
    async def before_gw_loop(self):
        await self.bot.wait_until_ready()


def setup(bot):
    bot.add_cog(Giveaways(bot))