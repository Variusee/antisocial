import disnake
from disnake.ext import commands
import asyncio
import time
import io
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).parent.parent))

COLOR_NEUTRAL = 0x2b2d31
COLOR_ERR = 0xF6C4C5
WHITE = (255, 255, 255, 255)
TEXT_COLOR = (59, 59, 59, 255)
ACTIVITY_ONLINE = (148, 130, 174, 255)
ACTIVITY_OFFLINE = (100, 100, 100, 255)

ASSETS_DIR = Path(__file__).parent.parent / "assets"
PROFILE_BG = ASSETS_DIR / "profile_bg.png"
FONT_PATH = ASSETS_DIR / "font.ttf"


def format_number(num: int) -> str:
    if num >= 1_000_000_000:
        return f"{num / 1_000_000_000:.1f} млрд"
    if num >= 1_000_000:
        return f"{num / 1_000_000:.1f} млн"
    if num >= 1_000:
        return f"{num / 1_000:.1f} тыс"
    return str(num)


def format_voice_time(seconds: int) -> str:
    if seconds == 0:
        return "0 м."
    
    years = seconds // 31536000
    months = (seconds % 31536000) // 2592000
    weeks = (seconds % 2592000) // 604800
    days = (seconds % 604800) // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    
    parts = []
    if years > 0:
        parts.append(f"{years} г.")
    if months > 0:
        parts.append(f"{months} мес.")
    if weeks > 0:
        parts.append(f"{weeks} нед.")
    if days > 0:
        parts.append(f"{days} д.")
    if hours > 0:
        parts.append(f"{hours} ч.")
    if minutes > 0:
        parts.append(f"{minutes} м.")
    
    return " ".join(parts[:2]) if parts else "0 м."


def draw_rounded_progress_bar(draw, x, y, width, height, percentage, fill_color, bg_color, radius):
    fill_width = int(width * percentage / 100)
    bg_color_with_alpha = (*bg_color[:3], int(255 * 0.3))
    draw.rounded_rectangle((x, y, x + width, y + height), radius=radius, fill=bg_color_with_alpha)
    if percentage > 0:
        draw.rounded_rectangle((x, y, x + fill_width, y + height), radius=radius, fill=fill_color)


def center_text(draw, text, font, x, y, color):
    bbox = draw.textbbox((0, 0), text, font=font)
    width = bbox[2] - bbox[0]
    draw.text((x - width // 2, y), text, fill=color, font=font)


class ProfileCard(commands.Cog):
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

    async def _show_profile(self, inter, target):
        if not PROFILE_BG.exists():
            return await inter.edit_original_response(
                embed=disnake.Embed(
                    title="—・Ошибка",
                    description=f"❌ Файл `profile_bg.png` не найден",
                    color=COLOR_NEUTRAL
                )
            )

        profile = await self.bot.pool.fetchrow("SELECT * FROM user_profiles WHERE user_id = $1", target.id)
        if not profile:
            await self.bot.pool.execute("""
                INSERT INTO user_profiles (user_id, created_at, balance, total_earned, voice_online, messages, reputation, level, exp, anticoin)
                VALUES ($1, $2, 0, 0, 0, 0, 0, 1, 0, 0)
            """, target.id, int(time.time()))
            profile = await self.bot.pool.fetchrow("SELECT * FROM user_profiles WHERE user_id = $1", target.id)

        stack = await self.bot.pool.fetchrow(
            "SELECT s.stack_name FROM stacks s JOIN stack_members sm ON s.stack_id = sm.stack_id WHERE sm.user_id = $1 AND s.status = 'active'",
            target.id
        )
        if not stack:
            stack = await self.bot.pool.fetchrow(
                "SELECT stack_name FROM stacks WHERE leader_id = $1 AND status = 'active'",
                target.id
            )

        partner_id = profile.get('marry_partner_id', 0) if profile else 0

        img = Image.open(str(PROFILE_BG)).convert("RGBA")
        draw = ImageDraw.Draw(img)

        font_title = await self.get_font(36)
        font_medium = await self.get_font(40)
        font_small = await self.get_font(16)
        font_lvl_num = await self.get_font(20)
        font_lvl_text = await self.get_font(10)
        font_activity = await self.get_font(18)

        try:
            avatar_bytes = await target.display_avatar.read()
            avatar = Image.open(io.BytesIO(avatar_bytes))
            avatar = avatar.resize((155, 155))
            mask = Image.new('L', (155, 155), 0)
            mask_draw = ImageDraw.Draw(mask)
            mask_draw.ellipse((0, 0, 155, 155), fill=255)
            img.paste(avatar, (354, 105), mask)
        except:
            pass

        center_text(draw, target.display_name[:20], font_title, 434, 305, WHITE)

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
        
        center_text(draw, activity_text, font_activity, 434, 345, activity_color)

        voice_online = await self.bot.pool.fetchval(
            "SELECT COALESCE(SUM(seconds), 0) FROM voice_hours WHERE user_id = $1",
            target.id
        ) or 0
        voice_text = format_voice_time(voice_online)
        center_text(draw, voice_text, font_medium, 143, 100, TEXT_COLOR)
        
        messages = profile.get('messages', 0) if profile else 0
        center_text(draw, format_number(messages), font_medium, 143, 213, TEXT_COLOR)
        
        balance = profile.get('balance', 0) if profile else 0
        center_text(draw, format_number(balance), font_medium, 143, 336, TEXT_COLOR)

        clan_name = stack['stack_name'][:20] if stack else "Отсутствует"
        center_text(draw, clan_name, font_medium, 744, 96, TEXT_COLOR)
        
        anticoin = profile.get('anticoin', 0) if profile else 0
        center_text(draw, format_number(anticoin), font_medium, 720, 214, TEXT_COLOR)
        
        rep = profile.get('reputation', 0) if profile else 0
        center_text(draw, str(rep), font_medium, 720, 338, TEXT_COLOR)

        level = profile.get('level', 1) if profile else 1
        exp = profile.get('exp', 0) if profile else 0
        exp_needed = 50 + (level * 50)
        exp_percent = min(100, int(exp / exp_needed * 100)) if exp_needed > 0 else 0

        bar_width = 180
        bar_height = 4
        bar_x = 434 - bar_width // 2
        bar_y = 420

        draw_rounded_progress_bar(draw, bar_x, bar_y, bar_width, bar_height, exp_percent, WHITE, (150, 150, 150, 150), 30)

        draw.text((bar_x - 30, bar_y - 15), str(level), fill=WHITE, font=font_lvl_num)
        draw.text((bar_x - 15, bar_y - 10), "lvl", fill=WHITE, font=font_lvl_text)

        draw.text((bar_x + bar_width + 5, bar_y - 15), str(level + 1), fill=WHITE, font=font_lvl_num)
        draw.text((bar_x + bar_width + 20, bar_y - 10), "lvl", fill=WHITE, font=font_lvl_text)

        exp_text = f"{format_number(exp)} / {format_number(exp_needed)} xp"
        center_text(draw, exp_text, font_small, 434, bar_y + 20, WHITE)

        if partner_id and partner_id != 0:
            partner = inter.guild.get_member(partner_id)
            if partner:
                partner_text = partner.display_name[:20]
            else:
                partner_text = f"ID: {partner_id}"
            center_text(draw, partner_text, font_medium, 720, 460, TEXT_COLOR)

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)

        file = disnake.File(buf, filename="profile.png")
        await inter.edit_original_response(content=None, file=file)

    @commands.slash_command(name="профиль", description="Показать профиль пользователя")
    async def profile(self, inter, пользователь: disnake.Member = None):
        target = пользователь or inter.author
        await inter.response.defer()
        await self._show_profile(inter, target)


def setup(bot):
    bot.add_cog(ProfileCard(bot))