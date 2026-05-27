import disnake
from disnake.ext import commands, tasks
import time
from datetime import datetime, timezone
import sys

sys.path.insert(0, "/root/antisocial")
from shared.config_manager import load_config

REDIS_PREFIX = "vt:session:"


def _today_key(ts: float | None = None) -> str:
    if ts is None:
        ts = time.time()
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def _midnight_after(ts: float) -> float:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    next_day = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    next_day_ts = next_day.timestamp() + 86400
    return next_day_ts


class VoiceHoursTracker(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._restored = False
        self.tick.start()

    def cog_unload(self):
        self.tick.cancel()

    async def _is_counted_channel(self, channel) -> bool:
        if not channel:
            return False
        if not isinstance(channel, (disnake.VoiceChannel, disnake.StageChannel)):
            return False
        cfg = await load_config()
        afk_id = cfg.settings.voice_stats.afk_channel_id
        if afk_id and channel.id == afk_id:
            return False
        counted_cats = cfg.settings.voice_stats.counted_category_ids
        if counted_cats and channel.category_id not in counted_cats:
            return False
        return True

    def _is_active_voice_state(self, voice_state) -> bool:
        if voice_state is None or voice_state.channel is None:
            return False
        if voice_state.mute or voice_state.deaf or voice_state.self_mute or voice_state.self_deaf:
            return False
        return True

    async def _get_session(self, user_id: int):
        if not self.bot.redis:
            return None, None
        try:
            raw = await self.bot.redis.get(f"{REDIS_PREFIX}{user_id}")
            if not raw:
                return None, None
            day_key, ts_str = raw.split(":", 1)
            return day_key, int(ts_str)
        except Exception:
            return None, None

    async def _start_session(self, user_id: int):
        if not self.bot.redis:
            return
        try:
            now = int(time.time())
            await self.bot.redis.set(f"{REDIS_PREFIX}{user_id}", f"{_today_key(now)}:{now}")
        except Exception:
            pass

    async def _drop_session(self, user_id: int):
        if not self.bot.redis:
            return
        try:
            await self.bot.redis.delete(f"{REDIS_PREFIX}{user_id}")
        except Exception:
            pass

    async def _add_seconds(self, guild_id: int, user_id: int, day_key: str, seconds: int):
        if seconds <= 0:
            return
        await self.bot.pool.execute("""
            INSERT INTO voice_hours (guild_id, user_id, day_key, seconds, updated_at)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (guild_id, user_id, day_key)
            DO UPDATE SET seconds = voice_hours.seconds + $4, updated_at = $5
        """, guild_id, user_id, day_key, seconds, int(time.time()))
        
        # Обновляем voice_online в профиле
        await self.bot.pool.execute("""
            INSERT INTO user_profiles (user_id, voice_online)
            VALUES ($1, $2)
            ON CONFLICT (user_id) DO UPDATE
            SET voice_online = user_profiles.voice_online + $2
        """, user_id, seconds)
        
        # Добавляем опыт (1 exp за минуту)
        exp_gain = seconds // 60
        if exp_gain > 0:
            await self.bot.pool.execute("""
                UPDATE user_profiles 
                SET exp = exp + $2 
                WHERE user_id = $1
            """, user_id, exp_gain)
            
            # Проверяем повышение уровня
            await self._check_level_up(user_id)

    async def _check_level_up(self, user_id: int):
        profile = await self.bot.pool.fetchrow("SELECT level, exp FROM user_profiles WHERE user_id = $1", user_id)
        if not profile:
            return
        
        level = profile['level']
        exp = profile['exp']
        level_up = False
        
        while exp >= level * 100:
            exp -= level * 100
            level += 1
            level_up = True
        
        if level_up:
            await self.bot.pool.execute("UPDATE user_profiles SET level = $2, exp = $3 WHERE user_id = $1", user_id, level, exp)

    async def _close_session(self, guild_id: int, user_id: int):
        day_key, started_ts = await self._get_session(user_id)
        if not day_key or not started_ts:
            return
        now = int(time.time())
        await self._spread_seconds(guild_id, user_id, day_key, started_ts, now)
        await self._drop_session(user_id)

    async def _flush_session(self, guild_id: int, user_id: int):
        day_key, started_ts = await self._get_session(user_id)
        if not day_key or not started_ts:
            return
        now = int(time.time())
        if now - started_ts < 60:
            return
        await self._spread_seconds(guild_id, user_id, day_key, started_ts, now)
        if self.bot.redis:
            try:
                await self.bot.redis.set(f"{REDIS_PREFIX}{user_id}", f"{_today_key(now)}:{now}")
            except Exception:
                pass

    async def _spread_seconds(self, guild_id: int, user_id: int, start_day: str, started_ts: int, end_ts: int):
        if end_ts <= started_ts:
            return
        cur_ts = started_ts
        cur_day = start_day
        while cur_ts < end_ts:
            mid = _midnight_after(cur_ts)
            seg_end = min(end_ts, int(mid))
            seconds = seg_end - cur_ts
            if seconds > 0:
                await self._add_seconds(guild_id, user_id, cur_day, seconds)
            cur_ts = seg_end
            cur_day = _today_key(cur_ts)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot:
            return
        guild_id = member.guild.id

        was_active = self._is_active_voice_state(before) and await self._is_counted_channel(before.channel)
        is_active = self._is_active_voice_state(after) and await self._is_counted_channel(after.channel)

        if not was_active and is_active:
            await self._start_session(member.id)
        elif was_active and not is_active:
            await self._close_session(guild_id, member.id)

    @commands.Cog.listener()
    async def on_ready(self):
        if self._restored:
            return
        self._restored = True
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            for ch in list(guild.voice_channels) + list(guild.stage_channels):
                if not await self._is_counted_channel(ch):
                    continue
                for m in ch.members:
                    if m.bot:
                        continue
                    if not self._is_active_voice_state(m.voice):
                        continue
                    cur_day, _ = await self._get_session(m.id)
                    if cur_day:
                        continue
                    await self._start_session(m.id)

    @tasks.loop(seconds=60)
    async def tick(self):
        if not self.bot.pool or not self.bot.redis:
            return
        try:
            cursor = 0
            while True:
                cursor, keys = await self.bot.redis.scan(cursor=cursor, match=f"{REDIS_PREFIX}*", count=100)
                for key in keys:
                    try:
                        parts = key.split(":", 1)
                        if len(parts) != 2:
                            continue
                        user_id = int(parts[1])
                        if not self.bot.guilds:
                            continue
                        guild_id = self.bot.guilds[0].id
                        await self._flush_session(guild_id, user_id)
                    except Exception:
                        pass
                if cursor == 0:
                    break
        except Exception as e:
            print(f"[voice_tracker.tick] {type(e).__name__}: {e}", flush=True)

    @tick.before_loop
    async def _before_tick(self):
        await self.bot.wait_until_ready()


def setup(bot):
    bot.add_cog(VoiceHoursTracker(bot))