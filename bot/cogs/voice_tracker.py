import disnake
from disnake.ext import commands, tasks
import time
import sys

sys.path.insert(0, "/root/antisocial")

REDIS_PREFIX = "stack_voice_session:"


class VoiceTracker(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.tick.start()
        self._restored = False

    def cog_unload(self):
        self.tick.cancel()

    async def _channel_to_stack(self, channel) -> int:
        if not channel or not isinstance(channel, disnake.VoiceChannel):
            return 0
        row = await self.bot.pool.fetchrow(
            "SELECT stack_id FROM stack_channels WHERE channel_id = $1",
            channel.id
        )
        if row:
            return row['stack_id']
        if channel.category_id:
            row = await self.bot.pool.fetchrow(
                "SELECT stack_id FROM stacks WHERE category_id = $1 AND status = 'active'",
                channel.category_id
            )
            if row:
                return row['stack_id']
        return 0

    async def _is_member_of_stack(self, user_id: int, stack_id: int) -> bool:
        if not stack_id:
            return False
        row = await self.bot.pool.fetchrow(
            "SELECT 1 FROM stack_members WHERE stack_id = $1 AND user_id = $2",
            stack_id, user_id
        )
        if row:
            return True
        row = await self.bot.pool.fetchrow(
            "SELECT 1 FROM stacks WHERE stack_id = $1 AND leader_id = $2",
            stack_id, user_id
        )
        return bool(row)

    async def _get_session(self, user_id: int):
        try:
            raw = await self.bot.redis.get(f"{REDIS_PREFIX}{user_id}")
            if not raw:
                return None, None
            stack_id_str, ts_str = raw.split(":", 1)
            return int(stack_id_str), int(ts_str)
        except Exception:
            return None, None

    async def _start_session(self, user_id: int, stack_id: int):
        try:
            await self.bot.redis.set(
                f"{REDIS_PREFIX}{user_id}",
                f"{stack_id}:{int(time.time())}"
            )
        except Exception:
            pass

    async def _close_session(self, user_id: int):
        stack_id, started_ts = await self._get_session(user_id)
        if not stack_id or not started_ts:
            return 0
        now_ts = int(time.time())
        delta = max(0, now_ts - started_ts)
        if delta > 0:
            try:
                await self.bot.pool.execute("""
                    INSERT INTO stack_voice_hours (stack_id, user_id, seconds, last_update)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (stack_id, user_id)
                    DO UPDATE SET seconds = stack_voice_hours.seconds + $3, last_update = $4
                """, stack_id, user_id, delta, now_ts)
            except Exception as e:
                print(f"[VoiceTracker] DB error: {e}", flush=True)
        try:
            await self.bot.redis.delete(f"{REDIS_PREFIX}{user_id}")
        except Exception:
            pass
        return delta

    async def _flush_session(self, user_id: int):
        stack_id, started_ts = await self._get_session(user_id)
        if not stack_id or not started_ts:
            return
        now_ts = int(time.time())
        delta = max(0, now_ts - started_ts)
        if delta < 60:
            return
        try:
            await self.bot.pool.execute("""
                INSERT INTO stack_voice_hours (stack_id, user_id, seconds, last_update)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (stack_id, user_id)
                DO UPDATE SET seconds = stack_voice_hours.seconds + $3, last_update = $4
            """, stack_id, user_id, delta, now_ts)
        except Exception as e:
            print(f"[VoiceTracker] DB error: {e}", flush=True)
        try:
            await self.bot.redis.set(f"{REDIS_PREFIX}{user_id}", f"{stack_id}:{now_ts}")
        except Exception:
            pass

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot:
            return

        before_stack = await self._channel_to_stack(before.channel) if before.channel else 0
        after_stack = await self._channel_to_stack(after.channel) if after.channel else 0

        if before_stack and not await self._is_member_of_stack(member.id, before_stack):
            before_stack = 0
        if after_stack and not await self._is_member_of_stack(member.id, after_stack):
            after_stack = 0

        if before_stack == after_stack:
            return

        if before_stack:
            await self._close_session(member.id)

        if after_stack:
            await self._start_session(member.id, after_stack)

    @commands.Cog.listener()
    async def on_ready(self):
        if self._restored:
            return
        self._restored = True
        await self.bot.wait_until_ready()
        for guild in self.bot.guilds:
            for ch in guild.voice_channels:
                stack_id = await self._channel_to_stack(ch)
                if not stack_id:
                    continue
                for m in ch.members:
                    if m.bot:
                        continue
                    if not await self._is_member_of_stack(m.id, stack_id):
                        continue
                    cur_stack, _ = await self._get_session(m.id)
                    if cur_stack == stack_id:
                        continue
                    await self._start_session(m.id, stack_id)

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
                        await self._flush_session(user_id)
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
    bot.add_cog(VoiceTracker(bot))