import disnake
from disnake.ext import commands
import sys

sys.path.insert(0, "/root/antisocial")


class MessageTracker(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot:
            return
        if not message.guild:
            return
        
        # Обновляем сообщения в профиле
        await self.bot.pool.execute("""
            INSERT INTO user_profiles (user_id, messages)
            VALUES ($1, 1)
            ON CONFLICT (user_id) DO UPDATE
            SET messages = user_profiles.messages + 1
        """, message.author.id)
        
        # Добавляем опыт (1 exp за сообщение)
        await self.bot.pool.execute("""
            UPDATE user_profiles 
            SET exp = exp + 1 
            WHERE user_id = $1
        """, message.author.id)
        
        # Проверяем повышение уровня
        await self._check_level_up(message.author.id)

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


def setup(bot):
    bot.add_cog(MessageTracker(bot))