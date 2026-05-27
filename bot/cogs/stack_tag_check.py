import disnake
from disnake.ext import commands, tasks
import asyncio
import time
import sys

sys.path.insert(0, "/root/antisocial")

from shared.config_manager import load_config


COLOR_NEUTRAL = 0x2b2d31
COLOR_OK = 0x9EE5B4
COLOR_ERR = 0xF6C4C5
COLOR_WARN = 0xF8E3A1


def err_embed(desc):
    return disnake.Embed(title="—・Ошибка", description=desc, color=COLOR_ERR)


async def _check_user_has_tag(bot, user_id: int) -> bool:
    try:
        from bot.cogs.clan_tag import db_is_wearing
        return await db_is_wearing(bot, user_id)
    except Exception:
        return False


class StackTagCheck(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._loop_lock = asyncio.Lock()
        self.warning_loop.start()

    def cog_unload(self):
        try:
            self.warning_loop.cancel()
        except Exception:
            pass

    async def _settings(self):
        cfg = await load_config()
        return cfg.settings.stacks.tag_check

    async def _kick_member_from_stack(self, guild: disnake.Guild, stack_id: int, user_id: int, reason: str):
        pool = self.bot.pool
        stack = await pool.fetchrow("SELECT stack_name, role_id, leader_id FROM stacks WHERE stack_id = $1", stack_id)
        if not stack:
            return False, None

        if user_id == stack['leader_id']:
            return False, stack

        await pool.execute("DELETE FROM stack_members WHERE stack_id = $1 AND user_id = $2", stack_id, user_id)
        await pool.execute("DELETE FROM stack_reviewers WHERE stack_id = $1 AND user_id = $2", stack_id, user_id)
        await pool.execute("DELETE FROM stack_tag_warnings WHERE user_id = $1", user_id)

        member = guild.get_member(user_id)
        if member and stack['role_id']:
            role = guild.get_role(stack['role_id'])
            if role and role in member.roles:
                try:
                    await member.remove_roles(role, reason=reason)
                except (disnake.Forbidden, disnake.HTTPException):
                    pass

        return True, stack

    async def _send_dm_safe(self, user, embed):
        try:
            await user.send(embed=embed)
            return True
        except (disnake.Forbidden, disnake.HTTPException):
            return False
        except Exception as e:
            print(f"[StackTagCheck:dm] {user.id}: {type(e).__name__}: {e}", flush=True)
            return False

    async def run_tag_check_for_stack(self, guild: disnake.Guild, stack_id: int, invoker_id: int) -> dict:
        pool = self.bot.pool
        ts = await self._settings()

        stack = await pool.fetchrow("SELECT stack_name, leader_id FROM stacks WHERE stack_id = $1", stack_id)
        if not stack:
            return {"error": "Стак не найден."}

        rows = await pool.fetch("SELECT user_id FROM stack_members WHERE stack_id = $1", stack_id)
        member_ids = [r['user_id'] for r in rows]

        whitelist = set(ts.whitelist_user_ids)
        violators = []
        already_warned = []

        existing = await pool.fetch(
            "SELECT user_id, deadline_at FROM stack_tag_warnings WHERE stack_id = $1",
            stack_id
        )
        existing_map = {r['user_id']: r['deadline_at'] for r in existing}

        for uid in member_ids:
            if uid == stack['leader_id']:
                continue
            if uid in whitelist:
                continue
            member = guild.get_member(uid)
            if not member:
                continue
            has_tag = await _check_user_has_tag(self.bot, uid)
            if has_tag:
                if uid in existing_map:
                    await pool.execute("DELETE FROM stack_tag_warnings WHERE user_id = $1", uid)
                continue

            if uid in existing_map:
                already_warned.append((member, existing_map[uid]))
            else:
                violators.append(member)

        now = int(time.time())
        deadline = now + ts.grace_hours * 3600
        for member in violators:
            await pool.execute(
                """
                INSERT INTO stack_tag_warnings (user_id, stack_id, warned_at, deadline_at, reminded_dm)
                VALUES ($1, $2, $3, $4, FALSE)
                ON CONFLICT (user_id) DO NOTHING
                """,
                member.id, stack_id, now, deadline
            )

        notify_ch = guild.get_channel(ts.notify_channel_id) if ts.notify_channel_id else None
        if notify_ch and (violators or already_warned):
            mentions = []
            for m in violators:
                mentions.append(m.mention)
            for m, _ in already_warned:
                mentions.append(m.mention)

            lines = [
                f"📢 **Проверка тега в стаке `{stack['stack_name']}`**",
                "",
                f"{', '.join(mentions)} — у вас **нет** тега гильдии **ANTI**.",
                "",
                f"У вас **{ts.grace_hours} часов**, чтобы поставить тег. Иначе вы будете автоматически кикнуты из стака.",
                f"_За {ts.reminder_hours_before} ч. до кика придёт напоминание в ЛС._",
            ]
            if already_warned:
                still_lines = []
                for m, d in already_warned:
                    still_lines.append(f"• {m.mention} — кик <t:{d}:R>")
                lines.append("")
                lines.append("**Уже были предупреждены:**")
                lines.extend(still_lines)

            try:
                await notify_ch.send("\n".join(lines))
            except (disnake.Forbidden, disnake.HTTPException) as e:
                print(f"[StackTagCheck:notify] send failed: {e}", flush=True)

        return {
            "stack_name": stack['stack_name'],
            "members_total": len(member_ids),
            "skipped_whitelist": sum(1 for uid in member_ids if uid in whitelist),
            "new_warned": len(violators),
            "still_warned": len(already_warned),
            "deadline_at": deadline,
        }

    @tasks.loop(minutes=5)
    async def warning_loop(self):
        if self._loop_lock.locked():
            return
        async with self._loop_lock:
            try:
                pool = self.bot.pool
                if not pool:
                    return
                ts = await self._settings()
                rows = await pool.fetch(
                    "SELECT user_id, stack_id, deadline_at, reminded_dm FROM stack_tag_warnings"
                )
                if not rows:
                    return

                now = int(time.time())
                whitelist = set(ts.whitelist_user_ids)

                guild = self.bot.guilds[0] if self.bot.guilds else None
                if not guild:
                    return

                notify_ch = guild.get_channel(ts.notify_channel_id) if ts.notify_channel_id else None

                for r in rows:
                    uid = r['user_id']
                    sid = r['stack_id']
                    deadline = r['deadline_at']
                    reminded = r['reminded_dm']

                    if uid in whitelist:
                        await pool.execute("DELETE FROM stack_tag_warnings WHERE user_id = $1", uid)
                        continue

                    has_tag = await _check_user_has_tag(self.bot, uid)
                    if has_tag:
                        await pool.execute("DELETE FROM stack_tag_warnings WHERE user_id = $1", uid)
                        continue

                    member = guild.get_member(uid)
                    if not member:
                        await pool.execute("DELETE FROM stack_tag_warnings WHERE user_id = $1", uid)
                        continue

                    in_stack = await pool.fetchrow(
                        "SELECT 1 FROM stack_members WHERE user_id = $1 AND stack_id = $2",
                        uid, sid
                    )
                    if not in_stack:
                        await pool.execute("DELETE FROM stack_tag_warnings WHERE user_id = $1", uid)
                        continue

                    if now >= deadline:
                        ok, stack = await self._kick_member_from_stack(
                            guild, sid, uid,
                            reason="StackTagCheck: автокик за отсутствие тега ANTI"
                        )
                        if ok and stack and notify_ch:
                            try:
                                await notify_ch.send(
                                    f"{member.mention} был автокикнут из стака **{stack['stack_name']}** за отсутствие тега ANTI."
                                )
                            except Exception as e:
                                print(f"[StackTagCheck:kick_notify] {e}", flush=True)
                        if ok and stack:
                            kick_dm = disnake.Embed(
                                title="—・Вы были исключены из стака",
                                description=(
                                    f"Вы были автоматически исключены из стака **{stack['stack_name']}** "
                                    f"за отсутствие тега гильдии **ANTI**.\n\n"
                                    f"_Если хотите вернуться — обратитесь к лидеру стака и поставьте тег._"
                                ),
                                color=COLOR_ERR
                            )
                            await self._send_dm_safe(member, kick_dm)
                        print(f"[StackTagCheck] автокик {uid} из стака {sid}", flush=True)
                        continue

                    seconds_left = deadline - now
                    reminder_threshold = ts.reminder_hours_before * 3600
                    if not reminded and seconds_left <= reminder_threshold and seconds_left > 0:
                        stack = await pool.fetchrow(
                            "SELECT stack_name, leader_id FROM stacks WHERE stack_id = $1", sid
                        )
                        if not stack:
                            continue
                        leader = guild.get_member(stack['leader_id'])
                        leader_text = leader.mention if leader else f"<@{stack['leader_id']}>"
                        notify_link = f"<#{ts.notify_channel_id}>" if ts.notify_channel_id else "канал уведомлений"

                        dm_embed = disnake.Embed(
                            title="⚠️  Напоминание: тег ANTI",
                            description=(
                                f"У вас осталось **{ts.reminder_hours_before} ч.** чтобы поставить тег "
                                f"гильдии **ANTI**, иначе вы будете автоматически кикнуты из стака "
                                f"**{stack['stack_name']}** (лидер: {leader_text}).\n\n"
                                f"**Как поставить тег:**\n"
                                f"\u200b**・** Откройте профиль сервера (правый верхний угол)\n"
                                f"\u200b**・** В разделе **Server Profile** найдите **Server Tag**\n"
                                f"\u200b**・** Выберите тег нашего сервера\n"
                                f"\u200b**・** Сохраните\n\n"
                                f"_Уведомления о статусе — в {notify_link}._"
                            ),
                            color=COLOR_WARN,
                            timestamp=disnake.utils.utcnow()
                        )
                        sent = await self._send_dm_safe(member, dm_embed)
                        if sent:
                            await pool.execute(
                                "UPDATE stack_tag_warnings SET reminded_dm = TRUE WHERE user_id = $1", uid
                            )
                        else:
                            print(f"[StackTagCheck:dm] {uid}: DM закрыт, не смогли напомнить", flush=True)

            except Exception as e:
                print(f"[StackTagCheck:loop] {type(e).__name__}: {e}", flush=True)

    @warning_loop.before_loop
    async def _before(self):
        await self.bot.wait_until_ready()
        await asyncio.sleep(20)

    @commands.Cog.listener()
    async def on_clan_tag_changed(self, member, is_wearing: bool):
        if not is_wearing:
            return
        try:
            await self.bot.pool.execute(
                "DELETE FROM stack_tag_warnings WHERE user_id = $1", member.id
            )
        except Exception:
            pass


def setup(bot):
    bot.add_cog(StackTagCheck(bot))
