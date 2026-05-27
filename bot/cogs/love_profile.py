import disnake
from disnake.ext import commands
import asyncio
import time
import io
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

COLOR_NEUTRAL = 0x2b2d31
COLOR_ERR = 0xF6C4C5
COLOR_LOVE = 0xFF69B4
TEXT_COLOR = (59, 59, 59, 255)
WHITE = (255, 255, 255, 255)
ACTIVITY_ONLINE = (148, 130, 174, 255)
ACTIVITY_OFFLINE = (100, 100, 100, 255)

ASSETS_DIR = Path(__file__).parent.parent / "assets"
LOVE_BG = ASSETS_DIR / "profile_love.png"
FONT_PATH = ASSETS_DIR / "font.ttf"


def format_love_time(seconds: int) -> str:
    if seconds == 0:
        return "0 д."
    
    years = seconds // 31536000
    months = (seconds % 31536000) // 2592000
    weeks = (seconds % 2592000) // 604800
    days = (seconds % 604800) // 86400
    
    parts = []
    if years > 0:
        parts.append(f"{years} г.")
    if months > 0:
        parts.append(f"{months} мес.")
    if weeks > 0:
        parts.append(f"{weeks} нед.")
    if days > 0:
        parts.append(f"{days} д.")
    
    return " ".join(parts[:2]) if parts else "0 д."


def center_text(draw, text, font, x, y, color):
    bbox = draw.textbbox((0, 0), text, font=font)
    width = bbox[2] - bbox[0]
    draw.text((x - width // 2, y), text, fill=color, font=font)


class MarryView(disnake.ui.View):
    def __init__(self, bot, proposer, target):
        super().__init__(timeout=60)
        self.bot = bot
        self.proposer = proposer
        self.target = target
        self.answered = False

    async def interaction_check(self, inter):
        return inter.author.id == self.target.id

    @disnake.ui.button(label="💍 Принять", style=disnake.ButtonStyle.success, emoji="💍")
    async def accept(self, button, inter):
        if self.answered:
            return await inter.response.send_message("Предложение уже обработано.", ephemeral=True)
        self.answered = True
        await self._execute_marriage(inter)

    @disnake.ui.button(label="💔 Отклонить", style=disnake.ButtonStyle.danger, emoji="💔")
    async def reject(self, button, inter):
        if self.answered:
            return await inter.response.send_message("Предложение уже обработано.", ephemeral=True)
        self.answered = True
        await inter.response.send_message(f"Вы отклонили предложение {self.proposer.mention}.", ephemeral=True)
        try:
            await self.proposer.send(f"{self.target.display_name} отклонил(а) ваше предложение.")
        except:
            pass
        self.stop()

    async def _execute_marriage(self, inter):
        now = int(time.time())
        
        balance = await self.bot.pool.fetchval("SELECT balance FROM user_profiles WHERE user_id = $1", self.proposer.id) or 0
        if balance < 5000:
            return await inter.response.send_message(f"У {self.proposer.mention} недостаточно средств (нужно 5000 монет).", ephemeral=True)
        
        await self.bot.pool.execute("UPDATE user_profiles SET balance = balance - 5000 WHERE user_id = $1", self.proposer.id)
        
        await self.bot.pool.execute("""
            INSERT INTO marriages (user1_id, user2_id, married_at, proposer_id, love_points, balance, is_active)
            VALUES ($1, $2, $3, $4, 0, 0, TRUE)
        """, self.proposer.id, self.target.id, now, self.proposer.id)
        
        await self.bot.pool.execute("UPDATE user_profiles SET marry_partner_id = $2 WHERE user_id = $1", self.proposer.id, self.target.id)
        await self.bot.pool.execute("UPDATE user_profiles SET marry_partner_id = $2 WHERE user_id = $1", self.target.id, self.proposer.id)
        
        embed = disnake.Embed(
            title="💍 Поздравляем!",
            description=f"{self.proposer.mention} и {self.target.mention} теперь муж и жена!",
            color=COLOR_LOVE
        )
        await inter.response.send_message(embed=embed)
        
        try:
            await self.proposer.send(f"💍 {self.target.display_name} принял(а) ваше предложение!")
        except:
            pass


class DivorceView(disnake.ui.View):
    def __init__(self, bot, marriage_id, user1_id, user2_id, author_id):
        super().__init__(timeout=30)
        self.bot = bot
        self.marriage_id = marriage_id
        self.user1_id = user1_id
        self.user2_id = user2_id
        self.author_id = author_id

    async def interaction_check(self, inter):
        return inter.author.id == self.author_id

    @disnake.ui.button(label="💔 Подтвердить развод", style=disnake.ButtonStyle.danger, emoji="💔")
    async def confirm(self, button, inter):
        await self.bot.pool.execute("UPDATE marriages SET is_active = FALSE, divorced_at = $2 WHERE id = $1", self.marriage_id, int(time.time()))
        await self.bot.pool.execute("UPDATE user_profiles SET marry_partner_id = 0 WHERE user_id = $1", self.user1_id)
        await self.bot.pool.execute("UPDATE user_profiles SET marry_partner_id = 0 WHERE user_id = $1", self.user2_id)
        
        await inter.response.send_message("💔 Брак расторгнут.", ephemeral=True)
        
        try:
            partner = await self.bot.fetch_user(self.user2_id if self.user1_id == self.author_id else self.user1_id)
            await partner.send(f"{inter.author.display_name} расторг(ла) ваш брак.")
        except:
            pass
        self.stop()

    @disnake.ui.button(label="❌ Отмена", style=disnake.ButtonStyle.secondary)
    async def cancel(self, button, inter):
        await inter.response.send_message("Развод отменён.", ephemeral=True)
        self.stop()


class GiveLoveModal(disnake.ui.Modal):
    def __init__(self, bot, marriage_id, author_id):
        self.bot = bot
        self.marriage_id = marriage_id
        self.author_id = author_id
        components = [
            disnake.ui.TextInput(
                label="Количество очков любви",
                placeholder="1-1000",
                custom_id="amount",
                style=disnake.TextInputStyle.short,
                max_length=4
            )
        ]
        super().__init__(title="💕 Подарить очки любви", components=components)

    async def callback(self, inter):
        try:
            amount = int(inter.text_values["amount"])
            if amount < 1 or amount > 1000:
                raise ValueError
        except:
            return await inter.response.send_message("❌ Введите число от 1 до 1000.", ephemeral=True)
        
        cost = amount * 50
        balance = await self.bot.pool.fetchval("SELECT balance FROM user_profiles WHERE user_id = $1", self.author_id) or 0
        
        if balance < cost:
            return await inter.response.send_message(f"❌ Недостаточно средств. Нужно {cost} монет.", ephemeral=True)
        
        await self.bot.pool.execute("UPDATE user_profiles SET balance = balance - $2 WHERE user_id = $1", self.author_id, cost)
        await self.bot.pool.execute("UPDATE marriages SET love_points = love_points + $2 WHERE id = $1", self.marriage_id, amount)
        
        marriage = await self.bot.pool.fetchrow("SELECT user1_id, user2_id FROM marriages WHERE id = $1", self.marriage_id)
        partner_id = marriage['user2_id'] if marriage['user1_id'] == self.author_id else marriage['user1_id']
        
        try:
            partner = await self.bot.fetch_user(partner_id)
            await partner.send(f"💕 {inter.author.display_name} подарил(а) вам **{amount}** очков любви!")
        except:
            pass
        
        await inter.response.send_message(f"✅ Вы подарили {amount} очков любви. Стоимость: {cost} монет.", ephemeral=True)


class LoveProfile(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.fonts = {}

    async def get_font(self, size: int):
        if size not in self.fonts:
            try:
                if FONT_PATH.exists():
                    self.fonts[size] = ImageFont.truetype(str(FONT_PATH), size)
                else:
                    self.fonts[size] = ImageFont.load_default()
            except:
                self.fonts[size] = ImageFont.load_default()
        return self.fonts[size]

    async def _show_love_profile(self, inter, target):
        if not LOVE_BG.exists():
            return await inter.edit_original_response(
                embed=disnake.Embed(
                    title="—・Ошибка",
                    description=f"❌ Файл `profile_love.png` не найден",
                    color=COLOR_NEUTRAL
                )
            )

        marriage = await self.bot.pool.fetchrow(
            "SELECT * FROM marriages WHERE (user1_id = $1 OR user2_id = $1) AND is_active = TRUE",
            target.id
        )

        if not marriage:
            return await inter.edit_original_response(
                embed=disnake.Embed(title="—・Ошибка", description=f"{target.display_name} не состоит в браке.", color=COLOR_ERR)
            )

        partner_id = marriage['user2_id'] if marriage['user1_id'] == target.id else marriage['user1_id']
        partner = inter.guild.get_member(partner_id)

        img = Image.open(str(LOVE_BG)).convert("RGBA")
        draw = ImageDraw.Draw(img)

        font_title = await self.get_font(36)
        font_medium = await self.get_font(45)
        font_activity = await self.get_font(18)

        avatar_x1 = 82
        avatar_x2 = 627
        avatar_center_x1 = avatar_x1 + 77
        avatar_center_x2 = avatar_x2 + 77
        avatar_y = 90

        nick_y = 270
        activity_y = 308

        try:
            avatar_bytes = await target.display_avatar.read()
            avatar = Image.open(io.BytesIO(avatar_bytes))
            avatar = avatar.resize((155, 155))
            mask = Image.new('L', (155, 155), 0)
            mask_draw = ImageDraw.Draw(mask)
            mask_draw.ellipse((0, 0, 155, 155), fill=255)
            img.paste(avatar, (avatar_x1, avatar_y), mask)
        except:
            pass

        if partner:
            try:
                partner_avatar_bytes = await partner.display_avatar.read()
                partner_avatar = Image.open(io.BytesIO(partner_avatar_bytes))
                partner_avatar = partner_avatar.resize((155, 155))
                img.paste(partner_avatar, (avatar_x2, avatar_y), mask)
            except:
                pass

        center_text(draw, target.display_name[:20], font_title, avatar_center_x1, nick_y, WHITE)

        if target.status == disnake.Status.online:
            activity_text = "в сети"
            activity_color = ACTIVITY_ONLINE
        elif target.status == disnake.Status.idle:
            activity_text = "не активен"
            activity_color = ACTIVITY_ONLINE
        elif target.status == disnake.Status.dnd:
            activity_text = "не беспокоить"
            activity_color = ACTIVITY_ONLINE
        else:
            activity_text = "не в сети"
            activity_color = ACTIVITY_OFFLINE
        center_text(draw, activity_text, font_activity, avatar_center_x1, activity_y, activity_color)

        if partner:
            center_text(draw, partner.display_name[:20], font_title, avatar_center_x2, nick_y, WHITE)

            if partner.status == disnake.Status.online:
                activity_text2 = "в сети"
                activity_color2 = ACTIVITY_ONLINE
            elif partner.status == disnake.Status.idle:
                activity_text2 = "не активен"
                activity_color2 = ACTIVITY_ONLINE
            elif partner.status == disnake.Status.dnd:
                activity_text2 = "не беспокоить"
                activity_color2 = ACTIVITY_ONLINE
            else:
                activity_text2 = "не в сети"
                activity_color2 = ACTIVITY_OFFLINE
            center_text(draw, activity_text2, font_activity, avatar_center_x2, activity_y, activity_color2)

        voice_online = await self.bot.pool.fetchval(
            "SELECT COALESCE(SUM(seconds), 0) FROM voice_hours WHERE user_id IN ($1, $2)",
            target.id, partner_id
        ) or 0
        
        if voice_online > 0:
            years = voice_online // 31536000
            days = (voice_online % 31536000) // 86400
            hours = (voice_online % 86400) // 3600
            minutes = (voice_online % 3600) // 60
            
            parts = []
            if years > 0:
                parts.append(f"{years} г.")
            if days > 0:
                parts.append(f"{days} д.")
            if hours > 0:
                parts.append(f"{hours} ч.")
            if minutes > 0:
                parts.append(f"{minutes} м.")
            
            voice_text = " ".join(parts) if parts else "0 м."
        else:
            voice_text = "0 м."

        top_pairs = await self.bot.pool.fetch("""
            SELECT user1_id, user2_id, 
                   (SELECT COALESCE(SUM(seconds), 0) FROM voice_hours WHERE user_id IN (user1_id, user2_id)) as total_seconds
            FROM marriages WHERE is_active = TRUE
            ORDER BY total_seconds DESC
            LIMIT 10
        """)
        
        rank = 1
        for i, pair in enumerate(top_pairs):
            if (pair['user1_id'] == target.id or pair['user1_id'] == partner_id or 
                pair['user2_id'] == target.id or pair['user2_id'] == partner_id):
                rank = i + 1
                break
        
        medals = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10"]
        rank_text = f"{medals[rank-1] if rank <= 3 else f'{rank}'}"

        center_text(draw, rank_text, font_medium, 434, 118, TEXT_COLOR)

        married_at = marriage.get('married_at', 0)
        if married_at:
            days_text = format_love_time(int(time.time()) - married_at)
        else:
            days_text = "0 д."
        center_text(draw, days_text, font_medium, 434, 257, TEXT_COLOR)

        center_text(draw, voice_text, font_medium, 434, 405, TEXT_COLOR)

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)

        file = disnake.File(buf, filename="love_profile.png")
        await inter.edit_original_response(content=None, file=file)

    @commands.slash_command(name="лав_профиль", description="Показать любовный профиль")
    async def love_profile(self, inter, пользователь: disnake.Member = None):
        target = пользователь or inter.author
        await inter.response.defer()
        await self._show_love_profile(inter, target)

    @commands.slash_command(name="заключить_брак", description="Предложить вступить в брак (стоимость 5000 монет)")
    async def marry_propose(self, inter, партнер: disnake.Member):
        await inter.response.defer(ephemeral=True)

        if партнер.id == inter.author.id:
            return await inter.edit_original_response("❌ Нельзя жениться на себе.")
        if партнер.bot:
            return await inter.edit_original_response("❌ Нельзя жениться на боте.")

        existing = await self.bot.pool.fetchrow(
            "SELECT * FROM marriages WHERE (user1_id = $1 OR user2_id = $1 OR user1_id = $2 OR user2_id = $2) AND is_active = TRUE",
            inter.author.id, партнер.id
        )
        if existing:
            return await inter.edit_original_response("❌ Один из вас уже в браке.")

        balance = await self.bot.pool.fetchval("SELECT balance FROM user_profiles WHERE user_id = $1", inter.author.id) or 0
        if balance < 5000:
            return await inter.edit_original_response(f"❌ Недостаточно средств. Нужно 5000 монет. У вас {balance}.")

        embed = disnake.Embed(
            title="💍 Предложение руки и сердца",
            description=f"{inter.author.mention} сделал(а) предложение {партнер.mention}!\n\nСтоимость брака: 5000 монет",
            color=COLOR_LOVE
        )
        
        view = MarryView(self.bot, inter.author, партнер)
        await партнер.send(embed=embed, view=view)
        await inter.edit_original_response(f"✅ Предложение отправлено {партнер.mention}. Оно будет действовать 60 секунд.")

    @commands.slash_command(name="развод", description="Расторгнуть брак")
    async def divorce(self, inter):
        await inter.response.defer(ephemeral=True)

        marriage = await self.bot.pool.fetchrow(
            "SELECT id, user1_id, user2_id FROM marriages WHERE (user1_id = $1 OR user2_id = $1) AND is_active = TRUE",
            inter.author.id
        )

        if not marriage:
            return await inter.edit_original_response("❌ Вы не состоите в браке.")

        view = DivorceView(self.bot, marriage['id'], marriage['user1_id'], marriage['user2_id'], inter.author.id)
        await inter.edit_original_response(
            "💔 **Подтверждение развода**\n\nВы уверены? Это действие нельзя отменить.",
            view=view
        )

    @commands.slash_command(name="подарить_любовь", description="Подарить очки любви партнёру (1 очко = 50 монет)")
    async def give_love(self, inter):
        await inter.response.defer(ephemeral=True)

        marriage = await self.bot.pool.fetchrow(
            "SELECT id FROM marriages WHERE (user1_id = $1 OR user2_id = $1) AND is_active = TRUE",
            inter.author.id
        )

        if not marriage:
            return await inter.edit_original_response("❌ Вы не состоите в браке.")

        await inter.response.send_modal(GiveLoveModal(self.bot, marriage['id'], inter.author.id))


def setup(bot):
    bot.add_cog(LoveProfile(bot))