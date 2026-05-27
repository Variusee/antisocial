import asyncio
import time
import traceback
import disnake
from disnake.ext import commands, tasks
import sys

sys.path.insert(0, "/root/antisocial")
from shared.config_manager import load_config

COLOR_GREEN = 0x9EE5B4
COLOR_RED = 0xF6C4C5
COLOR_ORANGE = 0xF8E3A1
COLOR_NEUTRAL = 0x9E9DEA
COLOR_BLUE = 0x5865F2

AUDIT_LOOKBACK_SECONDS = 5


def _audit_action(*names):
    for n in names:
        v = getattr(disnake.AuditLogAction, n, None)
        if v is not None:
            return v
    return None


AA_EVENT_CREATE = _audit_action("scheduled_event_create", "guild_scheduled_event_create")
AA_EVENT_UPDATE = _audit_action("scheduled_event_update", "guild_scheduled_event_update")
AA_EVENT_DELETE = _audit_action("scheduled_event_delete", "guild_scheduled_event_delete")


def _log_logger_error(where: str, exc: BaseException):
    print(f"[LOGGER:{where}] {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
    traceback.print_exc(file=sys.stderr)


class Logger(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._audit_cache_lock = asyncio.Lock()
        self._stats = {
            "voice_state_update": 0,
            "audit_log_entry": 0,
            "message_delete": 0,
            "message_edit": 0,
            "embeds_sent": 0,
            "embeds_failed": 0,
            "dest_none": 0,
            "last_event_ts": 0,
        }
        self._ws_state = "init"
        self.watchdog.start()

    def cog_unload(self):
        try:
            self.watchdog.cancel()
        except Exception:
            pass

    @tasks.loop(seconds=300)
    async def watchdog(self):
        try:
            now = int(time.time())
            since = (now - self._stats["last_event_ts"]) if self._stats["last_event_ts"] else 999999
            cache_size = len(getattr(self, "_audit_cache", {}))
            diag_size = len(getattr(self, "_diag_seen", set()))

            print(
                f"[LOGGER:watchdog] ws={self._ws_state} "
                f"voice_state={self._stats['voice_state_update']} "
                f"audit={self._stats['audit_log_entry']} "
                f"msg_del={self._stats['message_delete']} "
                f"msg_edit={self._stats['message_edit']} "
                f"sent={self._stats['embeds_sent']} "
                f"failed={self._stats['embeds_failed']} "
                f"dest_none={self._stats['dest_none']} "
                f"audit_cache={cache_size} diag={diag_size} "
                f"last_event={since}s_ago",
                flush=True
            )

            if self._stats["last_event_ts"] and since > 120:
                print(
                    f"[LOGGER:ALERT] ⚠️ События не приходят {since} сек! "
                    f"WS={self._ws_state}. Возможна блокировка loop или отвал WebSocket.",
                    flush=True
                )

            for k in ("voice_state_update", "audit_log_entry", "message_delete", "message_edit", "embeds_sent", "embeds_failed", "dest_none"):
                self._stats[k] = 0
        except Exception as e:
            try:
                _log_logger_error("watchdog", e)
            except Exception:
                print(f"[LOGGER:watchdog] error: {e}", flush=True)

    @watchdog.before_loop
    async def _before_watchdog(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_connect(self):
        self._ws_state = "connected"
        print(f"[LOGGER] WebSocket connected at {time.strftime('%H:%M:%S')}", flush=True)

    @commands.Cog.listener()
    async def on_disconnect(self):
        self._ws_state = "disconnected"
        print(f"[LOGGER] WebSocket DISCONNECTED at {time.strftime('%H:%M:%S')}", flush=True)

    @commands.Cog.listener()
    async def on_resumed(self):
        self._ws_state = "resumed"
        print(f"[LOGGER] WebSocket resumed at {time.strftime('%H:%M:%S')}", flush=True)

    async def _safe_send_embed(self, ch, embed, event_key="?"):
        if ch is None:
            return False
        try:
            await ch.send(embed=embed)
            self._stats["embeds_sent"] += 1
            return True
        except disnake.Forbidden:
            self._stats["embeds_failed"] += 1
            print(f"[LOGGER:send] '{event_key}' → канал {ch.id}: Forbidden (нет прав)", flush=True)
            return False
        except Exception as e:
            self._stats["embeds_failed"] += 1
            print(f"[LOGGER:send] '{event_key}' → канал {ch.id}: {type(e).__name__}: {e}", flush=True)
            return False

    async def _safe_fetch_user(self, user_id):
        try:
            return await asyncio.wait_for(self.bot.fetch_user(user_id), timeout=3.0)
        except asyncio.TimeoutError:
            print(f"[LOGGER:fetch_user] timeout для {user_id}", flush=True)
            return None
        except Exception:
            return None

    async def get_log_dest(self, guild, event_key):
        cfg = await load_config()
        log_cfg = cfg.logging.get(event_key)

        if not hasattr(self, '_diag_seen'):
            self._diag_seen = set()

        def _diag(reason):
            self._stats["dest_none"] += 1
            key = f"{event_key}:{reason}"
            if key in self._diag_seen:
                return
            self._diag_seen.add(key)
            print(f"[LOGGER:dest] '{event_key}' → {reason}", flush=True)

        if not log_cfg:
            _diag("нет в cfg.logging")
            return None
        if not log_cfg.enabled:
            _diag("enabled=False")
            return None
        ch_id = log_cfg.channel
        if not ch_id:
            ch_id = cfg.settings.moderation.server_log_channel_id
            if not ch_id:
                _diag(f"ch_id=0 и server_log_channel_id={cfg.settings.moderation.server_log_channel_id}")
                return None
        ch = guild.get_channel(int(ch_id))
        if not ch:
            _diag(f"канал {ch_id} не найден")
            return None
        return ch

    def build_embed(self, title, color, fields, icon_url=None, footer=None):
        embed = disnake.Embed(title=f"—・{title}", color=color, timestamp=disnake.utils.utcnow())
        if icon_url:
            embed.set_thumbnail(url=icon_url)
        for name, val, inline in fields:
            if val:
                embed.add_field(name=f"> {name}", value=val[:1024], inline=inline)
        if footer:
            embed.set_footer(text=footer)
        return embed

    def f_u(self, u):
        if not u:
            return "Неизвестно"
        return f"\u200b**・** {u.mention}\n\u200b**・** {getattr(u, 'name', 'Неизвестно')}\n\u200b**・** ID: `{u.id}`"

    def f_c(self, c):
        if not c:
            return "Неизвестно"
        mention = getattr(c, 'mention', f"#{c.id}")
        return f"\u200b**・** {mention}\n\u200b**・** {getattr(c, 'name', 'Неизвестно')}\n\u200b**・** ID: `{c.id}`"

    def f_r(self, r):
        if not r:
            return "Неизвестно"
        return f"\u200b**・** <@&{r.id}>\n\u200b**・** {r.name}\n\u200b**・** ID: `{r.id}`"

    def f_mod(self, mod):
        if not mod:
            return "\u200b**・** Неизвестно"
        return f"\u200b**・** {mod.mention} (`{mod.id}`)"

    async def find_audit_actor(self, guild, action, target_id, lookback=AUDIT_LOOKBACK_SECONDS):
        try:
            cache_key = (guild.id, action.value if hasattr(action, 'value') else int(action))
            now_ts = time.time()
            if not hasattr(self, '_audit_cache'):
                self._audit_cache = {}

            if len(self._audit_cache) > 200:
                old_keys = sorted(
                    self._audit_cache.keys(),
                    key=lambda k: self._audit_cache[k][0]
                )[:50]
                for k in old_keys:
                    self._audit_cache.pop(k, None)

            cached = self._audit_cache.get(cache_key)
            if cached and (now_ts - cached[0]) < 5:
                entries = cached[1]
            else:
                cutoff = now_ts - lookback
                entries = []
                async def _fetch_audit():
                    result = []
                    async for entry in guild.audit_logs(limit=10, action=action):
                        if entry.created_at.timestamp() < cutoff:
                            break
                        result.append(entry)
                    return result
                try:
                    entries = await asyncio.wait_for(_fetch_audit(), timeout=3.0)
                except asyncio.TimeoutError:
                    print(f"[LOGGER:audit] timeout fetching audit log for action={action}", flush=True)
                    entries = []
                self._audit_cache[cache_key] = (now_ts, entries)

            for entry in entries:
                t_id = getattr(entry.target, 'id', None) if entry.target else None
                if t_id == target_id:
                    return entry.user, entry
        except Exception as e:
            _log_logger_error("find_audit_actor", e)
        return None, None

    @commands.Cog.listener()
    async def on_member_join(self, member):
        if member.bot:
            return
        ch = await self.get_log_dest(member.guild, "member_join")
        if not ch:
            return
        fields = [
            ("Пользователь:", self.f_u(member), True),
            ("Создан аккаунт:", f"\u200b**・** <t:{int(member.created_at.timestamp())}:R>", True),
        ]
        try:
            await self._safe_send_embed(ch, embed=self.build_embed(
                "Пользователь присоединился", COLOR_GREEN, fields, member.display_avatar.url
            ))
        except Exception:
            pass

    @commands.Cog.listener()
    async def on_member_remove(self, member):
        if member.bot:
            return
        ch = await self.get_log_dest(member.guild, "member_leave")
        if not ch:
            return
        fields = [("Пользователь:", self.f_u(member), True)]
        r_txt = "\n".join([f"\u200b**・** <@&{r.id}>" for r in member.roles if r.id != member.guild.default_role.id])
        fields.append(("Роли:", r_txt[:1000] if r_txt else "\u200b**・** Нет", False))
        try:
            await self._safe_send_embed(ch, embed=self.build_embed("Пользователь вышел", COLOR_RED, fields, member.display_avatar.url))
        except Exception:
            pass

    @commands.Cog.listener()
    async def on_message_delete(self, msg):
        if not msg.guild or msg.author.bot:
            return
        ch = await self.get_log_dest(msg.guild, "message_delete")
        if not ch:
            return

        deleter = None
        try:
            cutoff = time.time() - AUDIT_LOOKBACK_SECONDS
            async def _fetch():
                d = None
                async for entry in msg.guild.audit_logs(limit=5, action=disnake.AuditLogAction.message_delete):
                    if entry.created_at.timestamp() < cutoff:
                        break
                    t_id = getattr(entry.target, 'id', None) if entry.target else None
                    if t_id == msg.author.id and entry.extra and getattr(entry.extra, 'channel', None) and entry.extra.channel.id == msg.channel.id:
                        d = entry.user
                        break
                return d
            try:
                deleter = await asyncio.wait_for(_fetch(), timeout=3.0)
            except asyncio.TimeoutError:
                print("[LOGGER:msg_delete] audit timeout", flush=True)
        except Exception:
            pass

        fields = [
            ("Автор:", self.f_u(msg.author), True),
            ("Канал:", self.f_c(msg.channel), True),
            ("Удалил:", self.f_mod(deleter) if deleter else "\u200b**・** Сам автор / неизвестно", False),
            ("Текст:", f"```{(msg.content or 'Пусто или медиа')[:1015]}```", False),
        ]
        try:
            await self._safe_send_embed(ch, embed=self.build_embed("Удаление сообщения", COLOR_RED, fields, msg.author.display_avatar.url))
        except Exception:
            pass

    @commands.Cog.listener()
    async def on_message_edit(self, before, after):
        if not before.guild or before.author.bot or before.content == after.content:
            return
        ch = await self.get_log_dest(before.guild, "message_edit")
        if not ch:
            return
        fields = [
            ("Автор:", self.f_u(before.author), True),
            ("Канал:", self.f_c(before.channel), True),
            ("Было:", f"```{(before.content or 'Пусто')[:1015]}```", False),
            ("Стало:", f"```{(after.content or 'Пусто')[:1015]}```", False),
            ("Ссылка:", f"\u200b**・** [Перейти к сообщению]({after.jump_url})", False),
        ]
        try:
            await self._safe_send_embed(ch, embed=self.build_embed("Изменение сообщения", COLOR_ORANGE, fields, before.author.display_avatar.url))
        except Exception:
            pass

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        guild = member.guild

        if before.channel is None and after.channel is not None:
            ch = await self.get_log_dest(guild, "voice_join")
            if ch:
                try:
                    await self._safe_send_embed(ch, embed=self.build_embed(
                        "Зашёл в голосовой канал", COLOR_GREEN,
                        [
                            ("Пользователь:", self.f_u(member), True),
                            ("Канал:", self.f_c(after.channel), True),
                        ],
                        member.display_avatar.url
                    ))
                except Exception:
                    pass
        elif before.channel is not None and after.channel is None:
            ch = await self.get_log_dest(guild, "voice_leave")
            if ch:
                try:
                    await self._safe_send_embed(ch, embed=self.build_embed(
                        "Вышел из голосового канала", COLOR_NEUTRAL,
                        [
                            ("Пользователь:", self.f_u(member), True),
                            ("Канал:", self.f_c(before.channel), True),
                        ],
                        member.display_avatar.url
                    ))
                except Exception:
                    pass
        elif before.channel and after.channel and before.channel != after.channel:
            ch = await self.get_log_dest(guild, "voice_move")
            if ch:
                mod, _ = await self.find_audit_actor(guild, disnake.AuditLogAction.member_move, member.id, lookback=3)
                fields = [
                    ("Пользователь:", self.f_u(member), True),
                    ("Канал до:", self.f_c(before.channel), True),
                    ("Канал после:", self.f_c(after.channel), False),
                ]
                if mod and mod.id != member.id:
                    fields.append(("Перенёс модератор:", self.f_mod(mod), False))
                try:
                    await self._safe_send_embed(ch, embed=self.build_embed(
                        "Переход в другой канал", COLOR_ORANGE, fields, member.display_avatar.url
                    ))
                except Exception:
                    pass

        if before.self_mute != after.self_mute:
            key = "voice_mute" if after.self_mute else "voice_unmute"
            title = "Выключил микрофон" if after.self_mute else "Включил микрофон"
            ch = await self.get_log_dest(guild, key)
            if ch:
                try:
                    fields = [
                        ("Пользователь:", self.f_u(member), True),
                        ("Канал:", self.f_c(after.channel or before.channel), True),
                    ]
                    await self._safe_send_embed(ch, embed=self.build_embed(title, COLOR_NEUTRAL, fields, member.display_avatar.url))
                except Exception:
                    pass

        if before.mute != after.mute:
            key = "voice_mute" if after.mute else "voice_unmute"
            title = "Заглушён модератором" if after.mute else "Размучен модератором"
            ch = await self.get_log_dest(guild, key)
            if ch:
                mod, _ = await self.find_audit_actor(guild, disnake.AuditLogAction.member_update, member.id, lookback=5)
                fields = [
                    ("Пользователь:", self.f_u(member), True),
                    ("Канал:", self.f_c(after.channel or before.channel), True),
                    ("Модератор:", self.f_mod(mod), False),
                ]
                try:
                    await self._safe_send_embed(ch, embed=self.build_embed(title, COLOR_RED, fields, member.display_avatar.url))
                except Exception:
                    pass

        if before.self_deaf != after.self_deaf:
            key = "voice_deafen" if after.self_deaf else "voice_undeafen"
            title = "Выключил наушники" if after.self_deaf else "Включил наушники"
            ch = await self.get_log_dest(guild, key)
            if ch:
                try:
                    fields = [
                        ("Пользователь:", self.f_u(member), True),
                        ("Канал:", self.f_c(after.channel or before.channel), True),
                    ]
                    await self._safe_send_embed(ch, embed=self.build_embed(title, COLOR_NEUTRAL, fields, member.display_avatar.url))
                except Exception:
                    pass

        if before.deaf != after.deaf:
            key = "voice_deafen" if after.deaf else "voice_undeafen"
            title = "Наушники выключены модератором" if after.deaf else "Наушники включены модератором"
            ch = await self.get_log_dest(guild, key)
            if ch:
                mod, _ = await self.find_audit_actor(guild, disnake.AuditLogAction.member_update, member.id, lookback=5)
                fields = [
                    ("Пользователь:", self.f_u(member), True),
                    ("Канал:", self.f_c(after.channel or before.channel), True),
                    ("Модератор:", self.f_mod(mod), False),
                ]
                try:
                    await self._safe_send_embed(ch, embed=self.build_embed(title, COLOR_RED, fields, member.display_avatar.url))
                except Exception:
                    pass

    @commands.Cog.listener()
    async def on_audit_log_entry_create(self, entry):
        guild, action, mod = entry.guild, entry.action, entry.user
        if mod and mod.bot and mod.id == self.bot.user.id:
            return

        try:
            if action == disnake.AuditLogAction.ban:
                ch = await self.get_log_dest(guild, "member_ban")
                if ch:
                    target = entry.target if not isinstance(entry.target, disnake.Object) else await self._safe_fetch_user(entry.target.id)
                    fields = [
                        ("Пользователь:", self.f_u(target), True),
                        ("Модератор:", self.f_mod(mod), True),
                    ]
                    if entry.reason:
                        fields.append(("Причина:", f"```{entry.reason}```", False))
                    await self._safe_send_embed(ch, embed=self.build_embed(
                        "Блокировка (бан)", COLOR_RED, fields,
                        target.display_avatar.url if target and hasattr(target, 'display_avatar') else None
                    ))

            elif action == disnake.AuditLogAction.unban:
                ch = await self.get_log_dest(guild, "member_unban")
                if ch:
                    target = entry.target if not isinstance(entry.target, disnake.Object) else await self._safe_fetch_user(entry.target.id)
                    fields = [
                        ("Пользователь:", self.f_u(target), True),
                        ("Модератор:", self.f_mod(mod), True),
                    ]
                    await self._safe_send_embed(ch, embed=self.build_embed(
                        "Разблокировка", COLOR_GREEN, fields,
                        target.display_avatar.url if target and hasattr(target, 'display_avatar') else None
                    ))

            elif action == disnake.AuditLogAction.kick:
                ch = await self.get_log_dest(guild, "member_leave")
                if ch:
                    target = entry.target if not isinstance(entry.target, disnake.Object) else await self._safe_fetch_user(entry.target.id)
                    fields = [
                        ("Пользователь:", self.f_u(target), True),
                        ("Модератор:", self.f_mod(mod), True),
                    ]
                    if entry.reason:
                        fields.append(("Причина:", f"```{entry.reason}```", False))
                    await self._safe_send_embed(ch, embed=self.build_embed(
                        "Кик с сервера", COLOR_RED, fields,
                        target.display_avatar.url if target and hasattr(target, 'display_avatar') else None
                    ))

            elif action == disnake.AuditLogAction.bot_add:
                ch = await self.get_log_dest(guild, "bot_add")
                if ch:
                    target = entry.target if not isinstance(entry.target, disnake.Object) else await self._safe_fetch_user(entry.target.id)
                    fields = [
                        ("Бот:", self.f_u(target), True),
                        ("Добавил:", self.f_mod(mod), True),
                    ]
                    await self._safe_send_embed(ch, embed=self.build_embed(
                        "Добавление бота", COLOR_GREEN, fields,
                        target.display_avatar.url if target and hasattr(target, 'display_avatar') else None
                    ))

            elif action == disnake.AuditLogAction.guild_update:
                ch = await self.get_log_dest(guild, "guild_update")
                if ch:
                    diffs = self._diff_attrs(entry.before, entry.after, ["name", "icon", "verification_level", "afk_timeout", "mfa_level"])
                    fields = [("Модератор:", self.f_mod(mod), True)]
                    if diffs:
                        fields.append(("Изменения:", "\n".join(diffs), False))
                    await self._safe_send_embed(ch, embed=self.build_embed(
                        "Изменение сервера", COLOR_ORANGE, fields,
                        guild.icon.url if guild.icon else None
                    ))

            elif action == disnake.AuditLogAction.member_update:
                target = entry.target
                if hasattr(entry.after, 'nick') and getattr(entry.before, 'nick', None) != getattr(entry.after, 'nick', None):
                    ch = await self.get_log_dest(guild, "member_nick_update")
                    if ch:
                        fields = [
                            ("Пользователь:", self.f_u(target), True),
                            ("Модератор:", self.f_mod(mod), True),
                            ("Было:", f"\u200b**・** {getattr(entry.before, 'nick', None) or 'Нет'}", True),
                            ("Стало:", f"\u200b**・** {getattr(entry.after, 'nick', None) or 'Нет'}", True),
                        ]
                        await self._safe_send_embed(ch, embed=self.build_embed(
                            "Изменение ника", COLOR_ORANGE, fields,
                            target.display_avatar.url if hasattr(target, 'display_avatar') else None
                        ))

            elif action == disnake.AuditLogAction.member_role_update:
                target = entry.target
                added = getattr(entry.after, 'roles', []) or []
                removed = getattr(entry.before, 'roles', []) or []
                if added:
                    ch = await self.get_log_dest(guild, "member_role_add")
                    if ch:
                        for r in added:
                            await self._safe_send_embed(ch, embed=self.build_embed(
                                "Выдача роли", COLOR_GREEN,
                                [
                                    ("Пользователь:", self.f_u(target), True),
                                    ("Модератор:", self.f_mod(mod), True),
                                    ("Роль:", self.f_r(r), False),
                                ],
                                target.display_avatar.url if hasattr(target, 'display_avatar') else None
                            ))
                if removed:
                    ch = await self.get_log_dest(guild, "member_role_remove")
                    if ch:
                        for r in removed:
                            await self._safe_send_embed(ch, embed=self.build_embed(
                                "Снятие роли", COLOR_NEUTRAL,
                                [
                                    ("Пользователь:", self.f_u(target), True),
                                    ("Модератор:", self.f_mod(mod), True),
                                    ("Роль:", self.f_r(r), False),
                                ],
                                target.display_avatar.url if hasattr(target, 'display_avatar') else None
                            ))

            elif action == disnake.AuditLogAction.channel_create:
                ch = await self.get_log_dest(guild, "channel_create")
                if ch:
                    name = getattr(entry.target, 'name', '?')
                    await self._safe_send_embed(ch, embed=self.build_embed(
                        "Создание канала", COLOR_GREEN,
                        [
                            ("Канал:", f"\u200b**・** <#{entry.target.id}>\n\u200b**・** {name}\n\u200b**・** ID: `{entry.target.id}`", True),
                            ("Модератор:", self.f_mod(mod), True),
                        ],
                        guild.icon.url if guild.icon else None
                    ))

            elif action == disnake.AuditLogAction.channel_update:
                ch = await self.get_log_dest(guild, "channel_update")
                if ch:
                    diffs = self._diff_attrs(entry.before, entry.after,
                                             ["name", "topic", "nsfw", "slowmode_delay", "bitrate", "user_limit", "rtc_region"])
                    if diffs or entry.before or entry.after:
                        target_id = entry.target.id if entry.target else 0
                        target_name = getattr(entry.target, 'name', '?')
                        fields = [
                            ("Канал:", f"\u200b**・** <#{target_id}>\n\u200b**・** {target_name}\n\u200b**・** ID: `{target_id}`", True),
                            ("Модератор:", self.f_mod(mod), True),
                        ]
                        if diffs:
                            fields.append(("Изменения:", "\n".join(diffs), False))
                        await self._safe_send_embed(ch, embed=self.build_embed(
                            "Изменение канала", COLOR_ORANGE, fields,
                            guild.icon.url if guild.icon else None
                        ))

            elif action == disnake.AuditLogAction.channel_delete:
                ch = await self.get_log_dest(guild, "channel_delete")
                if ch:
                    name = getattr(entry.before, 'name', '?')
                    target_id = entry.target.id if entry.target else 0
                    await self._safe_send_embed(ch, embed=self.build_embed(
                        "Удаление канала", COLOR_RED,
                        [
                            ("Канал:", f"\u200b**・** {name}\n\u200b**・** ID: `{target_id}`", True),
                            ("Модератор:", self.f_mod(mod), True),
                        ],
                        guild.icon.url if guild.icon else None
                    ))

            elif action in (
                disnake.AuditLogAction.overwrite_create,
                disnake.AuditLogAction.overwrite_update,
                disnake.AuditLogAction.overwrite_delete,
            ):
                ch = await self.get_log_dest(guild, "channel_update")
                if ch:
                    target_channel = entry.target
                    overwrite_target = self._extract_overwrite_target(entry, guild)
                    action_label = {
                        disnake.AuditLogAction.overwrite_create: ("➕ Добавлены права", COLOR_GREEN),
                        disnake.AuditLogAction.overwrite_update: ("✏️ Изменены права", COLOR_ORANGE),
                        disnake.AuditLogAction.overwrite_delete: ("➖ Удалены права", COLOR_RED),
                    }[action]
                    title, color = action_label

                    perm_diffs = self._diff_overwrites(entry.before, entry.after)

                    target_id = target_channel.id if target_channel else 0
                    target_name = getattr(target_channel, 'name', '?')
                    fields = [
                        ("Канал:", f"\u200b**・** <#{target_id}>\n\u200b**・** {target_name}\n\u200b**・** ID: `{target_id}`", True),
                        ("Модератор:", self.f_mod(mod), True),
                        ("Кому:", overwrite_target, False),
                    ]
                    if perm_diffs:
                        fields.append(("Изменения прав:", "\n".join(perm_diffs)[:1024], False))
                    await self._safe_send_embed(ch, embed=self.build_embed(
                        title, color, fields, guild.icon.url if guild.icon else None
                    ))

            elif action == disnake.AuditLogAction.thread_create:
                ch = await self.get_log_dest(guild, "thread_create")
                if ch:
                    await self._safe_send_embed(ch, embed=self.build_embed(
                        "Создание ветки", COLOR_GREEN,
                        [
                            ("Ветка:", f"\u200b**・** <#{entry.target.id}>\n\u200b**・** {getattr(entry.target, 'name', '?')}\n\u200b**・** ID: `{entry.target.id}`", True),
                            ("Модератор:", self.f_mod(mod), True),
                        ],
                        guild.icon.url if guild.icon else None
                    ))

            elif action == disnake.AuditLogAction.thread_update:
                ch = await self.get_log_dest(guild, "thread_update")
                if ch:
                    diffs = self._diff_attrs(entry.before, entry.after, ["name", "archived", "locked", "auto_archive_duration"])
                    fields = [
                        ("Ветка:", f"\u200b**・** <#{entry.target.id}>\n\u200b**・** {getattr(entry.target, 'name', '?')}", True),
                        ("Модератор:", self.f_mod(mod), True),
                    ]
                    if diffs:
                        fields.append(("Изменения:", "\n".join(diffs), False))
                    await self._safe_send_embed(ch, embed=self.build_embed(
                        "Изменение ветки", COLOR_ORANGE, fields,
                        guild.icon.url if guild.icon else None
                    ))

            elif action == disnake.AuditLogAction.thread_delete:
                ch = await self.get_log_dest(guild, "thread_delete")
                if ch:
                    target_id = entry.target.id if entry.target else 0
                    await self._safe_send_embed(ch, embed=self.build_embed(
                        "Удаление ветки", COLOR_RED,
                        [
                            ("Ветка:", f"\u200b**・** {getattr(entry.before, 'name', '?')}\n\u200b**・** ID: `{target_id}`", True),
                            ("Модератор:", self.f_mod(mod), True),
                        ],
                        guild.icon.url if guild.icon else None
                    ))

            elif action == disnake.AuditLogAction.role_create:
                ch = await self.get_log_dest(guild, "role_create")
                if ch:
                    await self._safe_send_embed(ch, embed=self.build_embed(
                        "Создание роли", COLOR_GREEN,
                        [
                            ("Роль:", f"\u200b**・** <@&{entry.target.id}>\n\u200b**・** {getattr(entry.target, 'name', '?')}\n\u200b**・** ID: `{entry.target.id}`", True),
                            ("Модератор:", self.f_mod(mod), True),
                        ],
                        guild.icon.url if guild.icon else None
                    ))

            elif action == disnake.AuditLogAction.role_update:
                ch = await self.get_log_dest(guild, "role_update")
                if ch:
                    diffs = self._diff_attrs(entry.before, entry.after, ["name", "color", "hoist", "mentionable", "permissions"])
                    fields = [
                        ("Роль:", f"\u200b**・** <@&{entry.target.id}>\n\u200b**・** {getattr(entry.target, 'name', '?')}", True),
                        ("Модератор:", self.f_mod(mod), True),
                    ]
                    if diffs:
                        fields.append(("Изменения:", "\n".join(diffs), False))
                    await self._safe_send_embed(ch, embed=self.build_embed(
                        "Изменение роли", COLOR_ORANGE, fields,
                        guild.icon.url if guild.icon else None
                    ))

            elif action == disnake.AuditLogAction.role_delete:
                ch = await self.get_log_dest(guild, "role_delete")
                if ch:
                    target_id = entry.target.id if entry.target else 0
                    await self._safe_send_embed(ch, embed=self.build_embed(
                        "Удаление роли", COLOR_RED,
                        [
                            ("Роль:", f"\u200b**・** {getattr(entry.before, 'name', '?')}\n\u200b**・** ID: `{target_id}`", True),
                            ("Модератор:", self.f_mod(mod), True),
                        ],
                        guild.icon.url if guild.icon else None
                    ))

            elif action == disnake.AuditLogAction.emoji_create:
                await self._simple_audit(guild, "emoji_create", "Создание эмодзи", COLOR_GREEN, mod,
                                          ("Эмодзи:", f"\u200b**・** `:{getattr(entry.target, 'name', '?')}:`"))
            elif action == disnake.AuditLogAction.emoji_update:
                await self._simple_audit(guild, "emoji_update", "Изменение эмодзи", COLOR_ORANGE, mod,
                                          ("Эмодзи:", f"\u200b**・** `:{getattr(entry.target, 'name', '?')}:`"))
            elif action == disnake.AuditLogAction.emoji_delete:
                await self._simple_audit(guild, "emoji_delete", "Удаление эмодзи", COLOR_RED, mod,
                                          ("Эмодзи:", f"\u200b**・** `:{getattr(entry.before, 'name', '?')}:`"))

            elif action == disnake.AuditLogAction.sticker_create:
                await self._simple_audit(guild, "sticker_create", "Создание стикера", COLOR_GREEN, mod,
                                          ("Стикер:", f"\u200b**・** {getattr(entry.target, 'name', '?')}"))
            elif action == disnake.AuditLogAction.sticker_update:
                await self._simple_audit(guild, "sticker_update", "Изменение стикера", COLOR_ORANGE, mod,
                                          ("Стикер:", f"\u200b**・** {getattr(entry.target, 'name', '?')}"))
            elif action == disnake.AuditLogAction.sticker_delete:
                await self._simple_audit(guild, "sticker_delete", "Удаление стикера", COLOR_RED, mod,
                                          ("Стикер:", f"\u200b**・** {getattr(entry.before, 'name', '?')}"))

            elif action == disnake.AuditLogAction.invite_create:
                await self._simple_audit(guild, "invite_create", "Создание приглашения", COLOR_GREEN, mod,
                                          ("Код:", f"\u200b**・** `{getattr(entry.target, 'code', '?')}`"))
            elif action == disnake.AuditLogAction.invite_delete:
                await self._simple_audit(guild, "invite_delete", "Удаление приглашения", COLOR_RED, mod,
                                          ("Код:", f"\u200b**・** `{getattr(entry.before, 'code', '?')}`"))

            elif AA_EVENT_CREATE is not None and action == AA_EVENT_CREATE:
                await self._simple_audit(guild, "event_create", "Создание мероприятия", COLOR_GREEN, mod,
                                          ("Название:", f"\u200b**・** {getattr(entry.target, 'name', '?')}"))
            elif AA_EVENT_UPDATE is not None and action == AA_EVENT_UPDATE:
                await self._simple_audit(guild, "event_update", "Изменение мероприятия", COLOR_ORANGE, mod,
                                          ("Название:", f"\u200b**・** {getattr(entry.target, 'name', '?')}"))
            elif AA_EVENT_DELETE is not None and action == AA_EVENT_DELETE:
                await self._simple_audit(guild, "event_delete", "Удаление мероприятия", COLOR_RED, mod,
                                          ("Название:", f"\u200b**・** {getattr(entry.before, 'name', '?')}"))

            elif action == disnake.AuditLogAction.webhook_create:
                await self._simple_audit(guild, "webhook_create", "Создание вебхука", COLOR_GREEN, mod,
                                          ("Имя:", f"\u200b**・** {getattr(entry.target, 'name', '?')}"))
            elif action == disnake.AuditLogAction.webhook_update:
                await self._simple_audit(guild, "webhook_update", "Изменение вебхука", COLOR_ORANGE, mod,
                                          ("Имя:", f"\u200b**・** {getattr(entry.target, 'name', '?')}"))
            elif action == disnake.AuditLogAction.webhook_delete:
                await self._simple_audit(guild, "webhook_delete", "Удаление вебхука", COLOR_RED, mod,
                                          ("Имя:", f"\u200b**・** {getattr(entry.before, 'name', '?')}"))

        except Exception as e:
            _log_logger_error(f"audit:{action}", e)

    async def _simple_audit(self, guild, event_key, title, color, mod, target_field):
        ch = await self.get_log_dest(guild, event_key)
        if not ch:
            return
        try:
            await self._safe_send_embed(ch, embed=self.build_embed(
                title, color,
                [target_field + (True,), ("Модератор:", self.f_mod(mod), True)],
                guild.icon.url if guild.icon else None
            ))
        except Exception:
            pass

    def _diff_attrs(self, before, after, keys):
        if not before or not after:
            return []
        diffs = []
        for key in keys:
            b = getattr(before, key, None)
            a = getattr(after, key, None)
            if b == a:
                continue
            label = self._attr_label(key)
            b_disp = self._fmt_attr_value(key, b)
            a_disp = self._fmt_attr_value(key, a)
            diffs.append(f"\u200b**・** **{label}:** {b_disp} → {a_disp}")
        return diffs

    def _attr_label(self, key):
        return {
            "name": "Имя",
            "topic": "Topic",
            "nsfw": "NSFW",
            "slowmode_delay": "Slowmode",
            "bitrate": "Битрейт",
            "user_limit": "Лимит юзеров",
            "rtc_region": "Регион",
            "color": "Цвет",
            "hoist": "Отображать отдельно",
            "mentionable": "Можно упоминать",
            "permissions": "Права роли",
            "icon": "Иконка",
            "verification_level": "Уровень верификации",
            "afk_timeout": "AFK таймаут",
            "mfa_level": "MFA",
            "archived": "Архивирован",
            "locked": "Заблокирован",
            "auto_archive_duration": "Авто-архив (мин)",
            "nick": "Ник",
        }.get(key, key)

    def _fmt_attr_value(self, key, val):
        if val is None or val == "":
            return "*пусто*"
        if key in ("nsfw", "hoist", "mentionable", "archived", "locked"):
            return "✅" if val else "❌"
        s = str(val)
        if len(s) > 100:
            s = s[:97] + "..."
        return f"`{s}`"

    def _extract_overwrite_target(self, entry, guild):
        try:
            extra = entry.extra
            if extra is None:
                return "\u200b**・** *неизвестно*"

            role = getattr(extra, 'role', None)
            if role:
                return f"\u200b**・** {role.mention if hasattr(role, 'mention') else f'<@&{role.id}>'} (роль)"
            member = getattr(extra, 'member', None)
            if member:
                return f"\u200b**・** {member.mention if hasattr(member, 'mention') else f'<@{member.id}>'} (пользователь)"

            target_id = getattr(extra, 'id', None)
            target_type = getattr(extra, 'type', None)
            if target_id is None:
                return "\u200b**・** *неизвестно*"
            target_id = int(target_id)

            type_str = str(target_type).lower() if target_type is not None else ""

            if "role" in type_str or type_str == "0":
                if guild:
                    role_obj = guild.get_role(target_id)
                    if role_obj:
                        return f"\u200b**・** <@&{target_id}> ({role_obj.name})"
                if guild and target_id == guild.id:
                    return "\u200b**・** @everyone"
                return f"\u200b**・** <@&{target_id}> (роль)"
            elif "member" in type_str or "user" in type_str or type_str == "1":
                return f"\u200b**・** <@{target_id}> (пользователь)"

            if guild:
                role_obj = guild.get_role(target_id)
                if role_obj:
                    return f"\u200b**・** <@&{target_id}> ({role_obj.name})"
                member_obj = guild.get_member(target_id)
                if member_obj:
                    return f"\u200b**・** <@{target_id}> ({member_obj.display_name})"

            return f"\u200b**・** ID: `{target_id}`"
        except Exception:
            return "\u200b**・** *неизвестно*"

    def _diff_overwrites(self, before, after):
        if not before and not after:
            return []
        try:
            b_allow = getattr(before, 'allow', None) if before else None
            b_deny = getattr(before, 'deny', None) if before else None
            a_allow = getattr(after, 'allow', None) if after else None
            a_deny = getattr(after, 'deny', None) if after else None

            lines = []
            def perm_names(perm_obj):
                if perm_obj is None:
                    return set()
                try:
                    return {name for name, val in perm_obj if val}
                except Exception:
                    return set()

            b_a = perm_names(b_allow)
            b_d = perm_names(b_deny)
            a_a = perm_names(a_allow)
            a_d = perm_names(a_deny)

            new_allow = a_a - b_a
            removed_allow = b_a - a_a
            new_deny = a_d - b_d
            removed_deny = b_d - a_d

            for p in sorted(new_allow):
                lines.append(f"\u200b**・** ✅ Разрешено: `{p}`")
            for p in sorted(removed_allow):
                lines.append(f"\u200b**・** ⚪ Снято разрешение: `{p}`")
            for p in sorted(new_deny):
                lines.append(f"\u200b**・** ❌ Запрещено: `{p}`")
            for p in sorted(removed_deny):
                lines.append(f"\u200b**・** ⚪ Снят запрет: `{p}`")

            if not lines:
                if before and not after:
                    lines.append("\u200b**・** Все права удалены")
                elif after and not before:
                    lines.append("\u200b**・** Все права назначены")
            return lines[:10]
        except Exception:
            return []


def setup(bot):
    bot.add_cog(Logger(bot))
