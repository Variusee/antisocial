import disnake
import os
import time
import asyncio
from disnake.ext import commands, tasks

from .staff import is_staff


class Status(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.status_message = None
        self.update_status.start()

    def cog_unload(self):
        self.update_status.cancel()

    async def _measure_ping(self) -> int:
        try:
            t0 = time.perf_counter()
            await asyncio.wait_for(
                self.bot.fetch_user(self.bot.user.id),
                timeout=5.0
            )
            return round((time.perf_counter() - t0) * 1000)
        except asyncio.TimeoutError:
            return -1
        except Exception:
            return -1

    async def generate_status_embed(self):
        ping_ms = await self._measure_ping()
        if ping_ms < 0:
            ping_text = "недоступен"
        else:
            ping_text = f"{ping_ms}ms"
        entrypoint = os.getenv("BOT_ENTRYPOINT", "")
        cogs_dir = os.path.join(os.path.dirname(os.path.abspath(entrypoint)), "cogs") if entrypoint else ""
        modules_status = []
        if cogs_dir and os.path.exists(cogs_dir):
            for fn in sorted(os.listdir(cogs_dir)):
                if fn.endswith(".py") and not fn.startswith("_"):
                    cog_name = fn[:-3]
                    is_loaded = any(ext.endswith(f".{cog_name}") for ext in self.bot.extensions.keys())
                    modules_status.append(f"\u200b**・** {'🟢' if is_loaded else '🔴'} `{cog_name}`")
        modules_text = "\n".join(modules_status) if modules_status else "\u200b**・** Нет модулей"

        embed = disnake.Embed(title="—・Статус Системы", color=0x2b2d31)
        bot_name = getattr(self.bot, "bot_name", "?").upper()
        embed.add_field(
            name="> Основные показатели:",
            value=f"\u200b**・** Процесс: **{bot_name}**\n\u200b**・** Пинг API: **{ping_text}**",
            inline=False
        )
        embed.add_field(name="> Локальные модули:", value=modules_text, inline=False)
        if self.bot.user and self.bot.user.avatar:
            embed.set_thumbnail(url=self.bot.user.avatar.url)
        return embed

    @commands.slash_command(name="статус", description="Системный статус процесса и модулей")
    async def bots_status(self, inter):
        if not await is_staff(inter.author.id):
            return await inter.response.send_message(
                embed=disnake.Embed(title="—・Ошибка", description="У вас нет прав.", color=0xF6C4C5),
                ephemeral=True
            )
        await inter.response.send_message("Processing...", ephemeral=True)

        old = await self.bot.pool.fetchrow(
            "SELECT channel_id, message_id FROM bot_status_msg WHERE bot_name = $1",
            self.bot.bot_name
        )
        if old:
            try:
                old_ch = self.bot.get_channel(old['channel_id']) or await self.bot.fetch_channel(old['channel_id'])
                if old_ch:
                    old_msg = await old_ch.fetch_message(old['message_id'])
                    await old_msg.delete()
            except Exception:
                pass

        embed = await self.generate_status_embed()
        self.status_message = await inter.channel.send(embed=embed)
        await self.bot.pool.execute(
            "INSERT INTO bot_status_msg (bot_name, channel_id, message_id) VALUES ($1, $2, $3) "
            "ON CONFLICT (bot_name) DO UPDATE SET channel_id = $2, message_id = $3",
            self.bot.bot_name, inter.channel.id, self.status_message.id
        )

    @tasks.loop(seconds=45)
    async def update_status(self):
        try:
            if not self.status_message:
                row = await self.bot.pool.fetchrow(
                    "SELECT channel_id, message_id FROM bot_status_msg WHERE bot_name = $1",
                    self.bot.bot_name
                )
                if row:
                    channel = self.bot.get_channel(row['channel_id']) or await self.bot.fetch_channel(row['channel_id'])
                    if channel:
                        self.status_message = await channel.fetch_message(row['message_id'])
            if self.status_message:
                embed = await self.generate_status_embed()
                await self.status_message.edit(content=None, embed=embed)
        except disnake.NotFound:
            self.status_message = None
            await self.bot.pool.execute("DELETE FROM bot_status_msg WHERE bot_name = $1", self.bot.bot_name)
        except Exception:
            pass

    @update_status.before_loop
    async def before(self):
        await self.bot.wait_until_ready()


def setup(bot):
    bot.add_cog(Status(bot))
