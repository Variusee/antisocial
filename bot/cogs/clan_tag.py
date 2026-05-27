import disnake
from disnake.ext import commands, tasks
import asyncio
import time
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/root/antisocial")
from shared.config_manager import load_config
from shared.staff import is_staff


COLOR_NEUTRAL = 0x2b2d31
COLOR_OK = 0x9EE5B4
COLOR_ERR = 0xF6C4C5
COLOR_WARN = 0xF8E3A1


def err_embed(desc):
    return disnake.Embed(title="—・Ошибка", description=desc, color=COLOR_ERR)


def ok_embed(desc):
    return disnake.Embed(title="—・Готово", description=desc, color=COLOR_OK)


AUTO_SCAN_HOURS_UTC = (0, 12)


def is_wearing_our_tag_via_obj(pg, our_guild_id: int, our_tag: str) -> bool:
    if pg is None or not our_tag:
        return False
    pg_tag = getattr(pg, "tag", None)
    if not pg_tag or pg_tag.upper() != our_tag.upper():
        return False
    enabled = getattr(pg, "identity_enabled", None)
    if enabled is not True:
        return False
    return True


def is_wearing_our_tag(user_or_member, our_guild_id: int, our_tag: str) -> bool:
    if not user_or_member or getattr(user_or_member, "bot", False):
        return False
    pg = getattr(user_or_member, "primary_guild", None)
    if not is_wearing_our_tag_via_obj(pg, our_guild_id, our_tag):
        return False
    flags = getattr(user_or_member, "flags", None)
    if flags is not None:
        try:
            if getattr(flags, "automod_quarantined_guild_tag", False):
                return False
        except AttributeError:
            pass
    return True


async def fetch_user_raw_primary_guild(bot, user_id: int) -> dict | None:
    try:
        data = await bot.http.request(
            disnake.http.Route("GET", "/users/{user_id}", user_id=user_id)
        )
        pg = data.get("primary_guild") if isinstance(data, dict) else None
        if not pg:
            return None
        return {
            "tag": pg.get("tag"),
            "identity_enabled": pg.get("identity_enabled"),
            "identity_guild_id": int(pg["identity_guild_id"]) if pg.get("identity_guild_id") else None,
            "badge": pg.get("badge"),
        }
    except disnake.NotFound:
        return None
    except Exception as e:
        print(f"[ClanTag:fetch_raw] {user_id}: {type(e).__name__}: {e}", flush=True)
        return None


def is_wearing_our_tag_via_raw(raw_pg: dict | None, our_guild_id: int, our_tag: str) -> bool:
    if not raw_pg:
        return False
    pg_gid = raw_pg.get("identity_guild_id")
    pg_tag = raw_pg.get("tag")
    pg_en = raw_pg.get("identity_enabled")

    if pg_gid != our_guild_id:
        return False
    if not pg_tag or not our_tag or pg_tag.upper() != our_tag.upper():
        return False
    if pg_en is not True:
        return False
    return True


async def db_is_wearing(bot, user_id: int) -> bool:
    try:
        row = await bot.pool.fetchrow(
            "SELECT is_wearing FROM clan_tag_state WHERE user_id = $1",
            user_id
        )
        return bool(row and row['is_wearing'])
    except Exception:
        return False


async def db_check_live(bot, user_id: int) -> bool:
    try:
        cog = bot.get_cog("ClanTag")
        if not cog:
            return False
        our_tag = getattr(cog, "_our_tag_cache", None)
        if not our_tag:
            return False
        if not bot.guilds:
            return False
        our_guild_id = bot.guilds[0].id

        raw_pg = await fetch_user_raw_primary_guild(bot, user_id)
        return is_wearing_our_tag_via_raw(raw_pg, our_guild_id, our_tag)
    except Exception:
        return False


class ClanTag(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._scan_lock = asyncio.Lock()
        self._sync_lock = asyncio.Lock()
        self._last_auto_scan_hour = -1
        self._scan_cancel = False
        self.periodic_sync.start()
        self.auto_scan.start()

    def cog_unload(self):
        for t in (self.periodic_sync, self.auto_scan):
            try:
                t.cancel()
            except Exception:
                pass

    async def _get_settings(self):
        cfg = await load_config()
        return cfg.settings.clan_tag

    async def _get_log_dest(self, guild, event_key):
        cfg = await load_config()
        log_cfg = cfg.logging.get(event_key)
        srv_log = cfg.settings.moderation.server_log_channel_id

        if log_cfg is not None:
            if not log_cfg.enabled:
                return None
            ch_id = log_cfg.channel or srv_log
        else:
            ch_id = srv_log

        if not ch_id:
            return None
        return guild.get_channel(int(ch_id))

    async def _send_log_event(self, guild, member, event_key, color):
        dest = await self._get_log_dest(guild, event_key)
        if not dest:
            print(f"[ClanTag:log] '{event_key}' для {member.id}: канал не найден — пропуск", flush=True)
            return
        title = "Тег гильдии надет" if event_key == "clan_tag_added" else "Тег гильдии снят"
        embed = disnake.Embed(
            title=f"—・{title}",
            color=color,
            timestamp=disnake.utils.utcnow()
        )
        embed.add_field(
            name="> Участник:",
            value=(
                f"\u200b**・** {member.mention}\n"
                f"\u200b**・** `{member}`\n"
                f"\u200b**・** ID: `{member.id}`"
            ),
            inline=False
        )
        if getattr(member, "display_avatar", None):
            embed.set_thumbnail(url=member.display_avatar.url)
        try:
            await dest.send(embed=embed)
            print(f"[ClanTag:log] ✅ '{event_key}' для {member.id} → канал {dest.id}", flush=True)
        except disnake.Forbidden:
            print(f"[ClanTag:log] ❌ Forbidden — нет прав на отправку в канал {dest.id}", flush=True)
        except Exception as e:
            print(f"[ClanTag:log] ❌ '{event_key}' для {member.id}: {type(e).__name__}: {e}", flush=True)

    async def _has_wearing_in_db(self, user_id: int) -> bool:
        row = await self.bot.pool.fetchrow(
            "SELECT is_wearing FROM clan_tag_state WHERE user_id = $1",
            user_id
        )
        return bool(row and row['is_wearing'])

    async def _set_wearing_in_db(self, user_id: int, is_wearing: bool):
        now = int(time.time())
        await self.bot.pool.execute(
            """
            INSERT INTO clan_tag_state (user_id, is_wearing, last_changed_at, first_detected_at)
            VALUES ($1, $2, $3, $3)
            ON CONFLICT (user_id) DO UPDATE
                SET is_wearing = $2,
                    last_changed_at = $3
            """,
            user_id, is_wearing, now
        )

    async def _apply_role(self, member: disnake.Member, role_id: int, should_have: bool, reason: str):
        if not role_id:
            return False
        role = member.guild.get_role(role_id)
        if not role:
            return False
        has_role = role in member.roles
        try:
            if should_have and not has_role:
                await member.add_roles(role, reason=reason)
                return True
            if not should_have and has_role:
                await member.remove_roles(role, reason=reason)
                return True
        except (disnake.Forbidden, disnake.HTTPException):
            return False
        return False

    async def _detect_our_tag(self, guild: disnake.Guild) -> str | None:
        if hasattr(self, "_our_tag_cache") and self._our_tag_cache:
            return self._our_tag_cache

        members = [m for m in guild.members if not m.bot][:200]
        for m in members:
            raw = await fetch_user_raw_primary_guild(self.bot, m.id)
            if raw:
                gid = raw.get("identity_guild_id")
                tag = raw.get("tag")
                if gid == guild.id and tag:
                    self._our_tag_cache = tag.upper()
                    print(f"[ClanTag] Detected our server tag: '{self._our_tag_cache}'", flush=True)
                    return self._our_tag_cache
            await asyncio.sleep(0.35)
        return None

    async def _detect_our_tag_quick(self, guild: disnake.Guild) -> str | None:
        if hasattr(self, "_our_tag_cache") and self._our_tag_cache:
            return self._our_tag_cache

        members = [m for m in guild.members if not m.bot][:30]
        for m in members:
            raw = await fetch_user_raw_primary_guild(self.bot, m.id)
            if raw:
                gid = raw.get("identity_guild_id")
                tag = raw.get("tag")
                if gid == guild.id and tag:
                    self._our_tag_cache = tag.upper()
                    print(f"[ClanTag] Detected our server tag (quick): '{self._our_tag_cache}'", flush=True)
                    return self._our_tag_cache
            await asyncio.sleep(0.35)
        return None

    async def _process_state_change(self, member: disnake.Member, is_wearing_now: bool, ct_settings, log_event: bool = True):
        if member.bot:
            return None
        was_wearing = await self._has_wearing_in_db(member.id)

        if is_wearing_now == was_wearing:
            if ct_settings.role_id:
                await self._apply_role(
                    member, ct_settings.role_id, is_wearing_now,
                    reason="ClanTag: ре-синхронизация роли"
                )
            return None

        action = "НАДЕЛ" if is_wearing_now else "СНЯЛ"
        print(
            f"[ClanTag:state] 🏷️ {member.id} ({member}) {action} тег "
            f"(было: is_wearing={was_wearing}, стало: {is_wearing_now})",
            flush=True
        )

        await self._set_wearing_in_db(member.id, is_wearing_now)

        if ct_settings.role_id:
            reason = "ClanTag: тег надет" if is_wearing_now else "ClanTag: тег снят"
            await self._apply_role(member, ct_settings.role_id, is_wearing_now, reason)

        if log_event:
            if is_wearing_now:
                await self._send_log_event(member.guild, member, "clan_tag_added", COLOR_OK)
            else:
                await self._send_log_event(member.guild, member, "clan_tag_removed", COLOR_WARN)

        try:
            self.bot.dispatch("clan_tag_changed", member, is_wearing_now)
        except Exception:
            pass

        return "added" if is_wearing_now else "removed"

    @commands.Cog.listener()
    async def on_ready(self):
        try:
            row = await self.bot.pool.fetchrow(
                "SELECT scan_id, current_index, total, started_at, updated_at "
                "FROM clan_tag_scan_state WHERE id = 1"
            )
            if row:
                age = int(time.time()) - row['updated_at']
                if age < 7200:
                    pct = round(row['current_index'] / row['total'] * 100) if row['total'] else 0
                    print(
                        f"[ClanTag:on_ready] ⚠️  Найден прерванный скан: "
                        f"scan_id={row['scan_id']}, прогресс {row['current_index']}/{row['total']} "
                        f"({pct}%), возраст {age // 60} мин.",
                        flush=True
                    )
                await self._clear_scan_progress()
        except Exception as e:
            print(f"[ClanTag:on_ready] check_scan_progress: {e}", flush=True)

        if hasattr(self, "_our_tag_cache") and self._our_tag_cache:
            return

        async def _bg_detect():
            await asyncio.sleep(5)
            for guild in self.bot.guilds:
                tag = await self._detect_our_tag(guild)
                if tag:
                    return

        try:
            asyncio.create_task(_bg_detect())
        except Exception as e:
            print(f"[ClanTag:on_ready] {type(e).__name__}: {e}", flush=True)

    @commands.Cog.listener()
    async def on_user_update(self, before: disnake.User, after: disnake.User):
        ct_settings = await self._get_settings()
        if not ct_settings.enabled:
            return

        before_pg = getattr(before, "primary_guild", None)
        after_pg = getattr(after, "primary_guild", None)

        def _pg_tuple(pg):
            if pg is None:
                return (None, None, None)
            return (
                getattr(pg, "identity_enabled", None),
                getattr(pg, "tag", None),
                getattr(pg, "identity_guild_id", None),
            )

        before_t = _pg_tuple(before_pg)
        after_t = _pg_tuple(after_pg)

        if before_t == after_t:
            return

        print(
            f"[ClanTag:on_user_update] {after.id} ({after}): primary_guild изменился "
            f"BEFORE={before_t} → AFTER={after_t}",
            flush=True
        )

        guild = None
        member = None
        for g in self.bot.guilds:
            m = g.get_member(after.id)
            if m:
                guild = g
                member = m
                break
        if not member:
            return

        our_tag = getattr(self, "_our_tag_cache", None)
        if not our_tag:
            our_tag = await self._detect_our_tag_quick(guild)
        if not our_tag:
            return

        try:
            after_pg = getattr(after, "primary_guild", None)
            after_tag = getattr(after_pg, "tag", None) if after_pg else None
            after_enabled = getattr(after_pg, "identity_enabled", None) if after_pg else None

            if after_tag and after_tag.upper() == our_tag.upper() and after_enabled is True:
                raw_pg = await fetch_user_raw_primary_guild(self.bot, after.id)
                is_wearing_now = is_wearing_our_tag_via_raw(raw_pg, guild.id, our_tag)
                if is_wearing_now:
                    flags = getattr(member, "flags", None)
                    if flags is not None:
                        try:
                            if getattr(flags, "automod_quarantined_guild_tag", False):
                                is_wearing_now = False
                        except AttributeError:
                            pass
            else:
                is_wearing_now = False

            await self._process_state_change(member, is_wearing_now, ct_settings)
        except Exception as e:
            print(f"[ClanTag:on_user_update] {type(e).__name__}: {e}", flush=True)

    @commands.Cog.listener()
    async def on_member_update(self, before: disnake.Member, after: disnake.Member):
        ct_settings = await self._get_settings()
        if not ct_settings.enabled:
            return

        our_tag = await self._detect_our_tag(after.guild) if not getattr(self, "_our_tag_cache", None) else self._our_tag_cache
        if not our_tag:
            return

        before_pg = getattr(before, "primary_guild", None)
        after_pg = getattr(after, "primary_guild", None)
        before_flags = getattr(before, "flags", None)
        after_flags = getattr(after, "flags", None)

        def _pg_tuple(pg):
            if pg is None:
                return (None, None, None)
            return (
                getattr(pg, "identity_enabled", None),
                getattr(pg, "tag", None),
                getattr(pg, "identity_guild_id", None),
            )

        before_t = _pg_tuple(before_pg)
        after_t = _pg_tuple(after_pg)

        if before_t == after_t and before_flags == after_flags:
            return

        if before_t != after_t:
            print(
                f"[ClanTag:on_member_update] {after.id} ({after}): primary_guild изменился "
                f"BEFORE={before_t} → AFTER={after_t}",
                flush=True
            )

        try:
            after_pg = getattr(after, "primary_guild", None)
            after_tag = getattr(after_pg, "tag", None) if after_pg else None
            after_enabled = getattr(after_pg, "identity_enabled", None) if after_pg else None

            if after_tag and after_tag.upper() == our_tag.upper() and after_enabled is True:
                raw_pg = await fetch_user_raw_primary_guild(self.bot, after.id)
                is_wearing_now = is_wearing_our_tag_via_raw(raw_pg, after.guild.id, our_tag)
                if is_wearing_now:
                    flags = getattr(after, "flags", None)
                    if flags is not None:
                        try:
                            if getattr(flags, "automod_quarantined_guild_tag", False):
                                is_wearing_now = False
                        except AttributeError:
                            pass
            else:
                is_wearing_now = False

            await self._process_state_change(after, is_wearing_now, ct_settings)
        except Exception as e:
            print(f"[ClanTag:on_member_update] {type(e).__name__}: {e}", flush=True)

    @commands.Cog.listener()
    async def on_member_remove(self, member: disnake.Member):
        try:
            await self.bot.pool.execute(
                "DELETE FROM clan_tag_state WHERE user_id = $1",
                member.id
            )
        except Exception:
            pass

    async def _sync_roles_from_db(self, guild: disnake.Guild, ct_settings, progress_cb=None):
        if not ct_settings.role_id:
            return 0, 0, 0

        added = removed = errors = 0
        rows = await self.bot.pool.fetch(
            "SELECT user_id, is_wearing FROM clan_tag_state"
        )
        total = len(rows)
        last_progress_ts = 0
        for i, r in enumerate(rows):
            member = guild.get_member(r['user_id'])
            if not member:
                continue
            try:
                changed = await self._apply_role(
                    member, ct_settings.role_id, r['is_wearing'],
                    reason="ClanTag: фоновая синхр-я роли"
                )
                if changed:
                    if r['is_wearing']:
                        added += 1
                    else:
                        removed += 1
            except Exception as e:
                errors += 1
                print(f"[ClanTag:sync_roles] {member.id}: {type(e).__name__}: {e}", flush=True)
            await asyncio.sleep(0)
            if progress_cb and (time.time() - last_progress_ts > 5):
                last_progress_ts = time.time()
                try:
                    await progress_cb(i + 1, total, added, removed)
                except Exception:
                    pass
        return added, removed, errors

    async def _verify_wearers_via_rest(self, guild: disnake.Guild, ct_settings) -> dict:
        our_tag = await self._detect_our_tag(guild)
        if not our_tag:
            return {"checked": 0, "still_wearing": 0, "no_longer": 0, "errors": 0}

        rows = await self.bot.pool.fetch(
            "SELECT user_id FROM clan_tag_state WHERE is_wearing = TRUE"
        )
        if not rows:
            return {"checked": 0, "still_wearing": 0, "no_longer": 0, "errors": 0}

        checked = still_wearing = no_longer = errors = 0

        for r in rows:
            user_id = r['user_id']
            member = guild.get_member(user_id)
            if not member:
                continue

            attempts = 0
            raw_pg = None
            fetch_failed = True
            while attempts < 3:
                try:
                    raw_pg = await fetch_user_raw_primary_guild(self.bot, user_id)
                    fetch_failed = False
                    break
                except disnake.NotFound:
                    fetch_failed = False
                    raw_pg = None
                    break
                except disnake.HTTPException as e:
                    if e.status == 429:
                        retry_after = float(getattr(e, "retry_after", 5)) or 5
                        await asyncio.sleep(retry_after)
                        attempts += 1
                        continue
                    if e.status >= 500:
                        await asyncio.sleep(2)
                        attempts += 1
                        continue
                    break
                except Exception:
                    attempts += 1
                    await asyncio.sleep(1)
                    continue

            if fetch_failed and attempts >= 3:
                errors += 1
                await asyncio.sleep(0.35)
                continue

            checked += 1
            is_wearing_now = is_wearing_our_tag_via_raw(raw_pg, guild.id, our_tag)

            if is_wearing_now:
                flags = getattr(member, "flags", None)
                if flags is not None:
                    try:
                        if getattr(flags, "automod_quarantined_guild_tag", False):
                            is_wearing_now = False
                    except AttributeError:
                        pass

            if is_wearing_now:
                still_wearing += 1
                if ct_settings.role_id:
                    await self._apply_role(
                        member, ct_settings.role_id, True,
                        reason="ClanTag: фоновая REST-проверка"
                    )
            else:
                no_longer += 1
                print(f"[ClanTag:verify] 🏷️ {member.id} ({member}) снял тег → обновляем БД + снимаем роль", flush=True)
                await self._process_state_change(
                    member, False, ct_settings, log_event=True
                )

            await asyncio.sleep(0.35)

        return {"checked": checked, "still_wearing": still_wearing, "no_longer": no_longer, "errors": errors}

    @tasks.loop(minutes=5)
    async def periodic_sync(self):
        try:
            ct_settings = await self._get_settings()
            if not ct_settings.enabled:
                return
            try:
                wanted = max(5, min(int(ct_settings.sync_interval_minutes), 1440))
                if self.periodic_sync.minutes != wanted:
                    self.periodic_sync.change_interval(minutes=wanted)
            except Exception:
                pass

            async with self._sync_lock:
                for guild in self.bot.guilds:
                    a, r, e = await self._sync_roles_from_db(guild, ct_settings)
                    if a or r or e:
                        print(
                            f"[ClanTag:periodic:roles] guild {guild.id}: added={a} removed={r} errors={e}",
                            flush=True
                        )

                if not self._scan_lock.locked():
                    for guild in self.bot.guilds:
                        stats = await self._verify_wearers_via_rest(guild, ct_settings)
                        if stats['no_longer'] or stats['errors']:
                            print(
                                f"[ClanTag:periodic:verify] guild {guild.id}: "
                                f"checked={stats['checked']} still={stats['still_wearing']} "
                                f"no_longer={stats['no_longer']} errors={stats['errors']}",
                                flush=True
                            )
        except Exception as e:
            print(f"[ClanTag:periodic] {type(e).__name__}: {e}", flush=True)

    @periodic_sync.before_loop
    async def _before_periodic(self):
        await self.bot.wait_until_ready()
        await asyncio.sleep(30)

    @tasks.loop(minutes=5)
    async def auto_scan(self):
        try:
            now = datetime.now(timezone.utc)
            current_hour = now.hour
            if current_hour not in AUTO_SCAN_HOURS_UTC:
                return
            if self._last_auto_scan_hour == current_hour:
                return

            ct_settings = await self._get_settings()
            if not ct_settings.enabled:
                return

            if self._scan_lock.locked():
                print("[ClanTag:auto_scan] Скан уже идёт, пропускаем", flush=True)
                return

            self._last_auto_scan_hour = current_hour
            print(f"[ClanTag:auto_scan] Запуск автоскана в {current_hour}:00 UTC", flush=True)

            async with self._scan_lock:
                for guild in self.bot.guilds:
                    stats = await self._full_scan(guild)
                    print(
                        f"[ClanTag:auto_scan] guild {guild.id}: "
                        f"total={stats['total']} wearers={stats['wearers']} "
                        f"+{stats['added']}/-{stats['removed']} errors={stats['errors']}",
                        flush=True
                    )
        except Exception as e:
            print(f"[ClanTag:auto_scan] {type(e).__name__}: {e}", flush=True)

    @auto_scan.before_loop
    async def _before_auto_scan(self):
        await self.bot.wait_until_ready()
        await asyncio.sleep(60)

    async def _save_scan_progress(self, scan_id: str, current_index: int, total: int, started_at: int):
        try:
            await self.bot.pool.execute(
                """
                INSERT INTO clan_tag_scan_state (id, scan_id, current_index, total, started_at, updated_at)
                VALUES (1, $1, $2, $3, $4, $5)
                ON CONFLICT (id) DO UPDATE
                    SET scan_id = $1, current_index = $2, total = $3, started_at = $4, updated_at = $5
                """,
                scan_id, current_index, total, started_at, int(time.time())
            )
        except Exception as e:
            print(f"[ClanTag:scan_progress] save: {e}", flush=True)

    async def _clear_scan_progress(self):
        try:
            await self.bot.pool.execute("DELETE FROM clan_tag_scan_state WHERE id = 1")
        except Exception:
            pass

    async def _full_scan(self, guild: disnake.Guild, progress_cb=None) -> dict:
        self._scan_cancel = False
        scan_id = f"scan_{int(time.time())}"
        started_at = int(time.time())

        ct_settings = await self._get_settings()

        our_tag = await self._detect_our_tag(guild)
        if not our_tag:
            print("[ClanTag:scan] Не удалось определить наш server tag.", flush=True)
            return {"total": 0, "wearers": 0, "added": 0, "removed": 0, "errors": 0, "cancelled": False}

        members = [m for m in guild.members if not m.bot]
        total = len(members)
        wearers_now = 0
        added = removed = errors = 0
        last_progress_ts = 0
        last_db_save_ts = 0
        cancelled = False

        await self._save_scan_progress(scan_id, 0, total, started_at)

        for i, member in enumerate(members):
            if self._scan_cancel:
                cancelled = True
                print(f"[ClanTag:scan] Отменён на индексе {i}/{total}", flush=True)
                break

            if time.time() - last_db_save_ts > 30:
                last_db_save_ts = time.time()
                await self._save_scan_progress(scan_id, i, total, started_at)

            attempts = 0
            raw_pg = None
            fetch_failed = True
            while attempts < 3:
                try:
                    raw_pg = await fetch_user_raw_primary_guild(self.bot, member.id)
                    fetch_failed = False
                    break
                except disnake.NotFound:
                    fetch_failed = False
                    raw_pg = None
                    break
                except disnake.HTTPException as e:
                    if e.status == 429:
                        retry_after = float(getattr(e, "retry_after", 5)) or 5
                        print(f"[ClanTag:scan] rate-limit, ждём {retry_after}s, попытка {attempts + 1}/3", flush=True)
                        await asyncio.sleep(retry_after)
                        attempts += 1
                        continue
                    if e.status >= 500:
                        print(f"[ClanTag:scan] {member.id}: HTTP {e.status}, retry {attempts + 1}/3", flush=True)
                        await asyncio.sleep(2)
                        attempts += 1
                        continue
                    print(f"[ClanTag:scan] {member.id}: HTTP {e.status}: {e}", flush=True)
                    break
                except Exception as e:
                    print(f"[ClanTag:scan] {member.id}: {type(e).__name__}: {e}", flush=True)
                    attempts += 1
                    await asyncio.sleep(1)
                    continue

            if fetch_failed and raw_pg is None and attempts >= 3:
                errors += 1
                continue

            try:
                is_wearing_now = is_wearing_our_tag_via_raw(raw_pg, guild.id, our_tag)

                if is_wearing_now:
                    flags = getattr(member, "flags", None)
                    if flags is not None:
                        try:
                            if getattr(flags, "automod_quarantined_guild_tag", False):
                                is_wearing_now = False
                        except AttributeError:
                            pass

                if is_wearing_now:
                    wearers_now += 1

                result = await self._process_state_change(
                    member, is_wearing_now, ct_settings, log_event=False
                )
                if result == "added":
                    added += 1
                elif result == "removed":
                    removed += 1

                await asyncio.sleep(0.35)

                if progress_cb and (time.time() - last_progress_ts > 5):
                    last_progress_ts = time.time()
                    try:
                        await progress_cb(i + 1, total, wearers_now)
                    except Exception:
                        pass
            except Exception as e:
                errors += 1
                print(f"[ClanTag:scan_process] {member.id}: {type(e).__name__}: {e}", flush=True)

        await self._clear_scan_progress()

        return {
            "total": total,
            "wearers": wearers_now,
            "added": added,
            "removed": removed,
            "errors": errors,
            "cancelled": cancelled,
        }

    async def full_scan(self, inter):
        if not inter.response.is_done():
            await inter.response.defer(ephemeral=True, with_message=True)
        if not await is_staff(inter.author.id):
            return await inter.edit_original_response(embed=err_embed("Доступ запрещён."))

        if self._scan_lock.locked():
            return await inter.edit_original_response(embed=err_embed("Сканирование уже идёт."))

        guild = inter.guild
        total_members = sum(1 for m in guild.members if not m.bot)
        eta_seconds = int(total_members * 0.35) + 5

        await inter.edit_original_response(embed=disnake.Embed(
            title="🔍  Сканирование запущено",
            description=(
                f"Участников для проверки: **{total_members}**\n"
                f"Примерное время: **{eta_seconds // 60} мин {eta_seconds % 60} сек**\n\n"
                f"_Прогресс будет обновляться._"
            ),
            color=COLOR_NEUTRAL
        ))

        async def progress(done, total, wearers):
            try:
                pct = round(done / total * 100) if total else 0
                bar_filled = int(pct / 5)
                bar = "█" * bar_filled + "░" * (20 - bar_filled)
                await inter.edit_original_response(embed=disnake.Embed(
                    title="🔍  Сканирование...",
                    description=(
                        f"`{bar}` **{pct}%**\n"
                        f"Проверено: **{done}** / **{total}**\n"
                        f"Найдено носителей: **{wearers}**"
                    ),
                    color=COLOR_NEUTRAL
                ))
            except Exception:
                pass

        async with self._scan_lock:
            stats = await self._full_scan(guild, progress_cb=progress)

        if stats['total'] == 0:
            return await inter.edit_original_response(embed=disnake.Embed(
                title="—・Не удалось определить тег гильдии",
                description=(
                    "Не нашлось ни одного участника с тегом нашей гильдии.\n"
                    "Проверьте настройки сервера → Server Profile → Tag."
                ),
                color=COLOR_WARN
            ))

        if stats.get("cancelled"):
            await inter.edit_original_response(embed=disnake.Embed(
                title="⚠️  Сканирование отменено",
                description=(
                    f"\u200b**・** Тег нашей гильдии: **{getattr(self, '_our_tag_cache', '?')}**\n"
                    f"\u200b**・** Носят тег сейчас (на момент отмены): **{stats['wearers']}**\n"
                    f"\u200b**・** Изменений в БД: **+{stats['added']}** / **-{stats['removed']}**"
                ),
                color=COLOR_WARN
            ))
            return

        await inter.edit_original_response(embed=disnake.Embed(
            title="✅  Сканирование завершено",
            description=(
                f"\u200b**・** Тег нашей гильдии: **{getattr(self, '_our_tag_cache', '?')}**\n"
                f"\u200b**・** Проверено участников: **{stats['total']}**\n"
                f"\u200b**・** Носят тег сейчас: **{stats['wearers']}**\n"
                f"\u200b**・** Изменений в БД: **+{stats['added']}** / **-{stats['removed']}**\n"
                f"\u200b**・** Ошибок: **{stats['errors']}**"
            ),
            color=COLOR_OK
        ))

    async def cancel_scan(self, inter):
        if not inter.response.is_done():
            await inter.response.defer(ephemeral=True, with_message=True)
        if not await is_staff(inter.author.id):
            return await inter.edit_original_response(embed=err_embed("Доступ запрещён."))

        if not self._scan_lock.locked():
            return await inter.edit_original_response(embed=disnake.Embed(
                title="—・Сканирование не запущено",
                color=COLOR_NEUTRAL
            ))

        self._scan_cancel = True
        await inter.edit_original_response(embed=disnake.Embed(
            title="🛑  Запрос на отмену принят",
            description="Сканирование остановится в течение нескольких секунд.",
            color=COLOR_WARN
        ))

    async def force_role_sync(self, inter):
        if not inter.response.is_done():
            await inter.response.defer(ephemeral=True, with_message=True)
        if not await is_staff(inter.author.id):
            return await inter.edit_original_response(embed=err_embed("Доступ запрещён."))

        ct_settings = await self._get_settings()
        if not ct_settings.role_id:
            return await inter.edit_original_response(embed=disnake.Embed(
                title="—・Роль не настроена",
                description="В `/настройки → 🏷️ Клан-тег` роль не задана.",
                color=COLOR_WARN
            ))

        if self._sync_lock.locked():
            return await inter.edit_original_response(embed=err_embed("Синхронизация уже идёт."))

        total_in_db = await self.bot.pool.fetchval(
            "SELECT COUNT(*) FROM clan_tag_state"
        ) or 0

        await inter.edit_original_response(embed=disnake.Embed(
            title="🔄  Синхронизация запущена",
            description=(
                f"\u200b**・** Записей в БД: **{total_in_db}**\n"
                f"\u200b**・** Прогресс будет обновляться..."
            ),
            color=COLOR_NEUTRAL
        ))

        async def progress(done, total, added, removed):
            try:
                pct = round(done / total * 100) if total else 0
                bar_filled = int(pct / 5)
                bar = "█" * bar_filled + "░" * (20 - bar_filled)
                await inter.edit_original_response(embed=disnake.Embed(
                    title="🔄  Синхронизация ролей...",
                    description=(
                        f"`{bar}` **{pct}%**\n"
                        f"\u200b**・** Обработано: **{done}** / **{total}**\n"
                        f"\u200b**・** Выдано ролей: **{added}**\n"
                        f"\u200b**・** Снято ролей: **{removed}**"
                    ),
                    color=COLOR_NEUTRAL
                ))
            except Exception:
                pass

        async with self._sync_lock:
            total_a = total_r = total_e = 0
            for guild in self.bot.guilds:
                a, r, e = await self._sync_roles_from_db(guild, ct_settings, progress_cb=progress)
                total_a += a
                total_r += r
                total_e += e

        print(
            f"[ClanTag:sync] ✅ Синхронизация завершена: "
            f"+{total_a}/-{total_r}/err={total_e} (всего в БД: {total_in_db})",
            flush=True
        )

        final_embed = disnake.Embed(
            title="✅  Синхронизация завершена",
            description=(
                f"\u200b**・** Записей в БД: **{total_in_db}**\n"
                f"\u200b**・** Выдано ролей: **{total_a}**\n"
                f"\u200b**・** Снято ролей: **{total_r}**\n"
                f"\u200b**・** Ошибок: **{total_e}**"
            ),
            color=COLOR_OK
        )
        for attempt in range(3):
            try:
                await inter.edit_original_response(embed=final_embed)
                break
            except disnake.HTTPException as e:
                if e.status == 429:
                    await asyncio.sleep(2 + attempt * 2)
                    continue
                break
            except Exception:
                break

    async def list_wearers(self, inter):
        if not inter.response.is_done():
            await inter.response.defer(ephemeral=True, with_message=True)
        if not await is_staff(inter.author.id):
            return await inter.edit_original_response(embed=err_embed("Доступ запрещён."))

        rows = await self.bot.pool.fetch(
            "SELECT user_id, last_changed_at FROM clan_tag_state WHERE is_wearing = TRUE ORDER BY last_changed_at DESC"
        )
        if not rows:
            return await inter.edit_original_response(embed=disnake.Embed(
                title="—・База пустая",
                description="Запустите `/сканировать_теги` чтобы заполнить базу.",
                color=COLOR_NEUTRAL
            ))

        guild = inter.guild
        wearers = []
        not_in_guild = 0
        for r in rows:
            m = guild.get_member(r['user_id'])
            if m:
                wearers.append((m, r['last_changed_at']))
            else:
                not_in_guild += 1

        if not wearers:
            return await inter.edit_original_response(embed=disnake.Embed(
                title="—・Никто не на сервере",
                description=f"В БД есть **{len(rows)}** записей, но никого из них нет на сервере.",
                color=COLOR_NEUTRAL
            ))

        view = WearersListView(wearers, inter.author.id, not_in_guild=not_in_guild)
        embed = view.build_embed()
        await inter.edit_original_response(embed=embed, view=view)

    async def check_user_tag(
        self,
        inter,
        участник: disnake.Member
    ):
        if not inter.response.is_done():
            await inter.response.defer(ephemeral=True, with_message=True)
        if not await is_staff(inter.author.id):
            return await inter.edit_original_response(embed=err_embed("Доступ запрещён."))

        raw_pg = await fetch_user_raw_primary_guild(self.bot, участник.id)
        row = await self.bot.pool.fetchrow(
            "SELECT is_wearing, last_changed_at FROM clan_tag_state WHERE user_id = $1",
            участник.id
        )

        our_tag = getattr(self, "_our_tag_cache", None)
        if not our_tag and raw_pg:
            if raw_pg.get("identity_guild_id") == inter.guild.id and raw_pg.get("tag"):
                our_tag = raw_pg["tag"].upper()
                self._our_tag_cache = our_tag
                print(f"[ClanTag] Detected our server tag from /проверить_тег: '{our_tag}'", flush=True)
        if not our_tag:
            our_tag = await self._detect_our_tag_quick(inter.guild)

        is_wearing = is_wearing_our_tag_via_raw(raw_pg, inter.guild.id, our_tag) if our_tag else None

        ct_settings = await self._get_settings()
        has_role = False
        if ct_settings.role_id:
            role = inter.guild.get_role(ct_settings.role_id)
            has_role = bool(role and role in участник.roles)

        if raw_pg is None:
            api_status = "❌ primary_guild = None (нет данных)"
            api_details = ""
        else:
            api_status = "✅ primary_guild получен"
            api_details = (
                f"\u200b**・** identity_guild_id: `{raw_pg.get('identity_guild_id')}`\n"
                f"\u200b**・** tag: `{raw_pg.get('tag')!r}`\n"
                f"\u200b**・** identity_enabled: `{raw_pg.get('identity_enabled')!r}`\n"
            )

        if row:
            db_status = (
                f"\u200b**・** is_wearing: **{row['is_wearing']}**\n"
                f"\u200b**・** Изменено: <t:{row['last_changed_at']}:R>"
            )
        else:
            db_status = "\u200b**・** Записи нет"

        verdict_color = COLOR_OK if is_wearing else (COLOR_WARN if is_wearing is None else COLOR_ERR)
        if is_wearing is None:
            verdict = "⚠️ Не могу определить (нет тега гильдии в кэше)"
        elif is_wearing:
            verdict = "✅ Носит наш тег"
        else:
            verdict = "❌ Не носит"

        embed = disnake.Embed(
            title=f"🔬  Диагностика тега — {участник}",
            color=verdict_color,
            timestamp=disnake.utils.utcnow()
        )
        embed.set_thumbnail(url=участник.display_avatar.url)
        embed.add_field(
            name="> Вердикт",
            value=f"\u200b**・** {verdict}",
            inline=False
        )
        embed.add_field(
            name="> Discord API",
            value=f"\u200b**・** {api_status}\n{api_details}",
            inline=False
        )
        embed.add_field(name="> База данных", value=db_status, inline=False)
        embed.add_field(
            name="> Роль",
            value=(
                f"\u200b**・** Настроена: {'<@&' + str(ct_settings.role_id) + '>' if ct_settings.role_id else 'нет'}\n"
                f"\u200b**・** У юзера сейчас: **{has_role}**"
            ),
            inline=False
        )
        embed.set_footer(text=f"Тег нашей гильдии: {our_tag or 'не определён'}")
        await inter.edit_original_response(embed=embed)


class WearersListView(disnake.ui.View):
    PER_PAGE = 20

    def __init__(self, wearers: list, invoker_id: int, not_in_guild: int = 0):
        super().__init__(timeout=300)
        self.wearers = wearers
        self.invoker_id = invoker_id
        self.not_in_guild = not_in_guild
        self.page = 0
        self._rebuild()

    async def interaction_check(self, inter):
        return inter.author.id == self.invoker_id

    def _total_pages(self):
        if not self.wearers:
            return 1
        return (len(self.wearers) - 1) // self.PER_PAGE + 1

    def build_embed(self):
        total = self._total_pages()
        start = self.page * self.PER_PAGE
        end = start + self.PER_PAGE
        chunk = self.wearers[start:end]

        lines = []
        for i, (m, ts) in enumerate(chunk, start=start + 1):
            lines.append(f"`#{i}` {m.mention} — <t:{ts}:R>")

        desc = "\n".join(lines)
        footer = []
        if self.not_in_guild:
            footer.append(f"*+ {self.not_in_guild} в БД, но уже не на сервере*")
        footer_str = "\n\n" + "\n".join(footer) if footer else ""

        embed = disnake.Embed(
            title=f"🏷️  Носители тега — всего {len(self.wearers)}",
            description=desc + footer_str,
            color=COLOR_NEUTRAL
        )
        embed.set_footer(text=f"Страница {self.page + 1}/{total}")
        return embed

    def _rebuild(self):
        self.clear_items()
        total = self._total_pages()
        if self.page >= total:
            self.page = total - 1
        if self.page < 0:
            self.page = 0
        if total > 1:
            prev_b = disnake.ui.Button(
                label="◀", style=disnake.ButtonStyle.secondary,
                disabled=self.page == 0, row=0
            )
            prev_b.callback = self._prev
            self.add_item(prev_b)
            page_b = disnake.ui.Button(
                label=f"Стр. {self.page + 1}/{total}",
                style=disnake.ButtonStyle.grey, disabled=True, row=0
            )
            self.add_item(page_b)
            next_b = disnake.ui.Button(
                label="▶", style=disnake.ButtonStyle.secondary,
                disabled=self.page >= total - 1, row=0
            )
            next_b.callback = self._next
            self.add_item(next_b)

    async def _prev(self, inter):
        self.page -= 1
        self._rebuild()
        await inter.response.edit_message(embed=self.build_embed(), view=self)

    async def _next(self, inter):
        self.page += 1
        self._rebuild()
        await inter.response.edit_message(embed=self.build_embed(), view=self)


def setup(bot):
    bot.add_cog(ClanTag(bot))
