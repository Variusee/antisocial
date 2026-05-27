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
COLOR_OK = 0x9EE5B4
WHITE = (255, 255, 255, 255)
TEXT_COLOR = (59, 59, 59, 255)
LEVEL_COLOR = (148, 130, 174, 255)
GOLD = (255, 215, 0, 255)

ASSETS_DIR = Path(__file__).parent.parent / "assets"
CLAN_BG = ASSETS_DIR / "clan_bg.png"
FONT_PATH = ASSETS_DIR / "font.ttf"


def format_number(num: int) -> str:
    if num >= 1_000_000_000:
        return f"{num / 1_000_000_000:.1f} млрд"
    if num >= 1_000_000:
        return f"{num / 1_000_000:.1f} млн"
    if num >= 1_000:
        return f"{num / 1_000:.1f} тыс"
    return str(num)


def draw_rounded_progress_bar(draw, x, y, width, height, percentage, fill_color, bg_color, radius):
    fill_width = int(width * percentage / 100)
    bg_color_with_alpha = (*bg_color[:3], int(255 * 0.3))
    draw.rounded_rectangle((x, y, x + width, y + height), radius=radius, fill=bg_color_with_alpha)
    if percentage > 0:
        draw.rounded_rectangle((x, y, x + fill_width, y + height), radius=radius, fill=fill_color)


class ClanSettingsView(disnake.ui.View):
    def __init__(self, bot, stack_id, author_id):
        super().__init__(timeout=180)
        self.bot = bot
        self.stack_id = stack_id
        self.author_id = author_id

    async def interaction_check(self, inter):
        return inter.author.id == self.author_id

    @disnake.ui.button(label="Название", style=disnake.ButtonStyle.secondary, emoji="📝", row=0)
    async def change_name(self, button, inter):
        await inter.response.send_modal(ClanNameModal(self.bot, self.stack_id))

    @disnake.ui.button(label="Аватарка", style=disnake.ButtonStyle.secondary, emoji="🖼️", row=0)
    async def change_icon(self, button, inter):
        await inter.response.send_modal(ClanIconModal(self.bot, self.stack_id))

    @disnake.ui.button(label="Заместители", style=disnake.ButtonStyle.secondary, emoji="👥", row=0)
    async def deputies(self, button, inter):
        await self._deputies_menu(inter)

    @disnake.ui.button(label="Передать стак", style=disnake.ButtonStyle.danger, emoji="👑", row=1)
    async def transfer(self, button, inter):
        await self._transfer_menu(inter)

    @disnake.ui.button(label="Казна", style=disnake.ButtonStyle.success, emoji="💰", row=1)
    async def treasury(self, button, inter):
        await self._treasury_menu(inter)

    @disnake.ui.button(label="Назад", style=disnake.ButtonStyle.grey, emoji="🔙", row=2)
    async def back(self, button, inter):
        await inter.response.edit_message(view=None, content="Меню закрыто")

    async def _deputies_menu(self, inter):
        deputies = await self.bot.pool.fetch(
            "SELECT user_id FROM stack_reviewers WHERE stack_id = $1",
            self.stack_id
        )
        deputy_list = "\n".join([f"<@{d['user_id']}>" for d in deputies]) if deputies else "Нет заместителей"

        embed = disnake.Embed(
            title="Заместители стака",
            description=f"Заместители могут управлять казной и настройками\n\n{deputy_list}",
            color=COLOR_NEUTRAL
        )
        view = DeputyManageView(self.bot, self.stack_id, self.author_id)
        await inter.response.edit_message(embed=embed, view=view)

    async def _transfer_menu(self, inter):
        embed = disnake.Embed(
            title="Передача стака",
            description="ВНИМАНИЕ!\n\nПосле передачи стака вы потеряете права лидера.\nНовый лидер получит все права управления.\n\nВыберите нового лидера:",
            color=COLOR_ERR
        )
        view = TransferStackView(self.bot, self.stack_id, self.author_id)
        await inter.response.edit_message(embed=embed, view=view)

    async def _treasury_menu(self, inter):
        stack = await self.bot.pool.fetchrow(
            "SELECT balance, stack_name FROM stacks WHERE stack_id = $1",
            self.stack_id
        )
        embed = disnake.Embed(
            title=f"Казна стака {stack['stack_name']}",
            description=f"Баланс казны: **{format_number(stack['balance'])}** монет\n\n"
                       f"/казна <сумма> - пополнить казну\n"
                       f"/казна_списать <сумма> - списать с казны (только лидер/заместитель)",
            color=COLOR_OK
        )
        await inter.response.edit_message(embed=embed, view=None)


class ClanNameModal(disnake.ui.Modal):
    def __init__(self, bot, stack_id):
        self.bot = bot
        self.stack_id = stack_id
        components = [
            disnake.ui.TextInput(
                label="Новое название стака",
                placeholder="Введите новое название",
                custom_id="name",
                style=disnake.TextInputStyle.short,
                max_length=32,
                min_length=2
            )
        ]
        super().__init__(title="Смена названия стака", components=components)

    async def callback(self, inter):
        new_name = inter.text_values["name"]

        existing = await self.bot.pool.fetchrow(
            "SELECT stack_id FROM stacks WHERE stack_name = $1 AND stack_id != $2 AND status = 'active'",
            new_name, self.stack_id
        )
        if existing:
            return await inter.response.send_message("Стак с таким названием уже существует", ephemeral=True)

        old_stack = await self.bot.pool.fetchrow(
            "SELECT stack_name, role_id, category_id FROM stacks WHERE stack_id = $1",
            self.stack_id
        )

        await self.bot.pool.execute(
            "UPDATE stacks SET stack_name = $1 WHERE stack_id = $2",
            new_name, self.stack_id
        )

        if old_stack['role_id']:
            role = inter.guild.get_role(old_stack['role_id'])
            if role:
                try:
                    await role.edit(name=new_name)
                except:
                    pass

        if old_stack['category_id']:
            category = inter.guild.get_channel(old_stack['category_id'])
            if category:
                try:
                    await category.edit(name=new_name)
                except:
                    pass

        await inter.response.send_message(f"Название стака изменено на **{new_name}**\nРоль и категория обновлены", ephemeral=True)


class ClanIconModal(disnake.ui.Modal):
    def __init__(self, bot, stack_id):
        self.bot = bot
        self.stack_id = stack_id
        components = [
            disnake.ui.TextInput(
                label="URL аватарки",
                placeholder="https://example.com/image.png",
                custom_id="url",
                style=disnake.TextInputStyle.short,
                max_length=200
            )
        ]
        super().__init__(title="Смена аватарки стака", components=components)

    async def callback(self, inter):
        url = inter.text_values["url"]
        await self.bot.pool.execute(
            "UPDATE stacks SET icon_url = $1 WHERE stack_id = $2",
            url, self.stack_id
        )
        await inter.response.send_message("Аватарка стака обновлена", ephemeral=True)


class DeputyManageView(disnake.ui.View):
    def __init__(self, bot, stack_id, author_id):
        super().__init__(timeout=180)
        self.bot = bot
        self.stack_id = stack_id
        self.author_id = author_id

    async def interaction_check(self, inter):
        return inter.author.id == self.author_id

    @disnake.ui.user_select(placeholder="Добавить заместителя", min_values=1, max_values=1, row=0)
    async def add_deputy(self, select, inter):
        member = select.values[0]

        is_member = await self.bot.pool.fetchval(
            "SELECT 1 FROM stack_members WHERE stack_id = $1 AND user_id = $2",
            self.stack_id, member.id
        )
        if not is_member:
            return await inter.response.send_message("Пользователь не состоит в стаке", ephemeral=True)

        existing = await self.bot.pool.fetchval(
            "SELECT 1 FROM stack_reviewers WHERE stack_id = $1 AND user_id = $2",
            self.stack_id, member.id
        )
        if existing:
            return await inter.response.send_message("Пользователь уже заместитель", ephemeral=True)

        await self.bot.pool.execute(
            "INSERT INTO stack_reviewers (stack_id, user_id, added_at, added_by) VALUES ($1, $2, $3, $4)",
            self.stack_id, member.id, int(time.time()), inter.author.id
        )

        deputies = await self.bot.pool.fetch(
            "SELECT user_id FROM stack_reviewers WHERE stack_id = $1",
            self.stack_id
        )
        deputy_list = "\n".join([f"<@{d['user_id']}>" for d in deputies]) if deputies else "Нет заместителей"

        embed = disnake.Embed(
            title="Заместители стака",
            description=f"Заместители могут управлять казной и настройками\n\n{deputy_list}",
            color=COLOR_NEUTRAL
        )
        await inter.response.edit_message(embed=embed, view=self)

    @disnake.ui.user_select(placeholder="Удалить заместителя", min_values=1, max_values=1, row=1)
    async def remove_deputy(self, select, inter):
        member = select.values[0]

        await self.bot.pool.execute(
            "DELETE FROM stack_reviewers WHERE stack_id = $1 AND user_id = $2",
            self.stack_id, member.id
        )

        deputies = await self.bot.pool.fetch(
            "SELECT user_id FROM stack_reviewers WHERE stack_id = $1",
            self.stack_id
        )
        deputy_list = "\n".join([f"<@{d['user_id']}>" for d in deputies]) if deputies else "Нет заместителей"

        embed = disnake.Embed(
            title="Заместители стака",
            description=f"Заместители могут управлять казной и настройками\n\n{deputy_list}",
            color=COLOR_NEUTRAL
        )
        await inter.response.edit_message(embed=embed, view=self)

    @disnake.ui.button(label="Назад", style=disnake.ButtonStyle.grey, emoji="🔙", row=2)
    async def back(self, button, inter):
        await inter.response.edit_message(view=ClanSettingsView(self.bot, self.stack_id, self.author_id))


class TransferStackView(disnake.ui.View):
    def __init__(self, bot, stack_id, author_id):
        super().__init__(timeout=60)
        self.bot = bot
        self.stack_id = stack_id
        self.author_id = author_id
        self.confirmed = False

    async def interaction_check(self, inter):
        return inter.author.id == self.author_id

    @disnake.ui.user_select(placeholder="Выберите нового лидера", min_values=1, max_values=1, row=0)
    async def select_leader(self, select, inter):
        if self.confirmed:
            return await inter.response.send_message("Передача уже выполнена", ephemeral=True)

        member = select.values[0]

        is_member = await self.bot.pool.fetchval(
            "SELECT 1 FROM stack_members WHERE stack_id = $1 AND user_id = $2",
            self.stack_id, member.id
        )
        if not is_member:
            return await inter.response.send_message("Пользователь не состоит в стаке", ephemeral=True)

        self.confirmed = True
        await self.bot.pool.execute(
            "UPDATE stacks SET leader_id = $1 WHERE stack_id = $2",
            member.id, self.stack_id
        )

        await self.bot.pool.execute(
            "DELETE FROM stack_reviewers WHERE stack_id = $1 AND user_id = $2",
            self.stack_id, member.id
        )

        await inter.response.send_message(f"Стак передан {member.mention}", ephemeral=True)

    @disnake.ui.button(label="Отмена", style=disnake.ButtonStyle.secondary, emoji="❌", row=1)
    async def cancel(self, button, inter):
        await inter.response.edit_message(content="Передача отменена", view=None, embed=None)


class ClanProfile(commands.Cog):
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

    async def _show_clan_profile(self, inter, stack):
        if not CLAN_BG.exists():
            return await inter.edit_original_response(
                embed=disnake.Embed(
                    title="Ошибка",
                    description="Файл clan_bg.png не найден",
                    color=COLOR_NEUTRAL
                )
            )

        members = await self.bot.pool.fetch(
            "SELECT user_id FROM stack_members WHERE stack_id = $1",
            stack['stack_id']
        )
        members_count = len(members)

        leader = inter.guild.get_member(stack['leader_id'])
        leader_name = leader.display_name if leader else f"ID: {stack['leader_id']}"

        deputies = await self.bot.pool.fetch(
            "SELECT user_id FROM stack_reviewers WHERE stack_id = $1",
            stack['stack_id']
        )

        top_stacks = await self.bot.pool.fetch("""
            SELECT stack_id, exp, level FROM stacks WHERE status = 'active' ORDER BY exp DESC LIMIT 10
        """)
        rank = 1
        for i, s in enumerate(top_stacks):
            if s['stack_id'] == stack['stack_id']:
                rank = i + 1
                break

        img = Image.open(str(CLAN_BG)).convert("RGBA")
        draw = ImageDraw.Draw(img)

        font_medium = await self.get_font(40)
        font_title = await self.get_font(36)
        font_small = await self.get_font(24)

        if stack.get('icon_url'):
            try:
                import aiohttp
                async with aiohttp.ClientSession() as session:
                    async with session.get(stack['icon_url']) as resp:
                        if resp.status == 200:
                            icon_bytes = await resp.read()
                            icon = Image.open(io.BytesIO(icon_bytes))
                            icon = icon.resize((160, 160))
                            mask = Image.new('L', (160, 160), 0)
                            mask_draw = ImageDraw.Draw(mask)
                            mask_draw.ellipse((0, 0, 160, 160), fill=255)
                            img.paste(icon, (551, 116), mask)
            except:
                pass

        if leader:
            try:
                leader_avatar_bytes = await leader.display_avatar.read()
                leader_avatar = Image.open(io.BytesIO(leader_avatar_bytes))
                leader_avatar = leader_avatar.resize((100, 100))
                mask = Image.new('L', (100, 100), 0)
                mask_draw = ImageDraw.Draw(mask)
                mask_draw.ellipse((0, 0, 100, 100), fill=255)
                img.paste(leader_avatar, (259, 113), mask)
            except:
                pass

        draw.text((634, 310), stack['stack_name'][:25], fill=TEXT_COLOR, font=font_medium)

        draw.text((259, 113), leader_name[:20], fill=TEXT_COLOR, font=font_title)

        draw.text((115, 335), str(stack['level']), fill=LEVEL_COLOR, font=font_medium)

        exp_needed = 50 + (stack['level'] * 50)
        exp_percent = min(100, int(stack['exp'] / exp_needed * 100)) if exp_needed > 0 else 0
        bar_x = 60
        bar_y = 355
        bar_width = 100
        bar_height = 6
        draw_rounded_progress_bar(draw, bar_x, bar_y, bar_width, bar_height, exp_percent, LEVEL_COLOR, (150, 150, 150, 150), 4)

        draw.text((236, 225), format_number(stack['balance']), fill=TEXT_COLOR, font=font_medium)

        draw.text((365, 316), str(members_count), fill=TEXT_COLOR, font=font_medium)

        draw.text((648, 369), f"#{rank}", fill=TEXT_COLOR, font=font_medium)

        deputy_list = ", ".join([f"<@{d['user_id']}>" for d in deputies[:2]]) if deputies else "Нет"
        if len(deputies) > 2:
            deputy_list += f" и ещё {len(deputies) - 2}"
        draw.text((630, 290), deputy_list[:30], fill=TEXT_COLOR, font=font_small)

        member_list = []
        for m in members[:5]:
            member_obj = inter.guild.get_member(m['user_id'])
            if member_obj:
                member_list.append(member_obj.display_name[:15])
        member_text = ", ".join(member_list) if member_list else "Нет"
        if len(members) > 5:
            member_text += f" и ещё {len(members) - 5}"
        draw.text((630, 250), member_text[:35], fill=TEXT_COLOR, font=font_small)

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)

        file = disnake.File(buf, filename="clan_profile.png")
        await inter.edit_original_response(content=None, file=file)

    @commands.slash_command(name="клан_профиль", description="Показать профиль клана")
    async def clan_profile(self, inter, название: str = None):
        await inter.response.defer()

        if название:
            stack = await self.bot.pool.fetchrow(
                "SELECT * FROM stacks WHERE stack_name = $1 AND status = 'active'",
                название
            )
        else:
            stack = await self.bot.pool.fetchrow(
                "SELECT s.* FROM stacks s JOIN stack_members sm ON s.stack_id = sm.stack_id WHERE sm.user_id = $1 AND s.status = 'active'",
                inter.author.id
            )
            if not stack:
                stack = await self.bot.pool.fetchrow(
                    "SELECT * FROM stacks WHERE leader_id = $1 AND status = 'active'",
                    inter.author.id
                )

        if not stack:
            return await inter.edit_original_response(
                embed=disnake.Embed(title="Ошибка", description="Клан не найден", color=COLOR_ERR)
            )

        await self._show_clan_profile(inter, stack)

    @commands.slash_command(name="настройка_стака", description="Настройка стака")
    async def clan_settings(self, inter):
        await inter.response.defer(ephemeral=True)

        stack = await self.bot.pool.fetchrow(
            "SELECT * FROM stacks WHERE leader_id = $1 AND status = 'active'",
            inter.author.id
        )

        if not stack:
            return await inter.edit_original_response(
                embed=disnake.Embed(title="Ошибка", description="Вы не являетесь лидером стака", color=COLOR_ERR)
            )

        embed = disnake.Embed(
            title=f"Настройки стака — {stack['stack_name']}",
            description=f"**Название:** `{stack['stack_name']}`\n"
                       f"**Аватарка:** {'✅' if stack.get('icon_url') else '❌'}\n"
                       f"**Казна:** `{format_number(stack['balance'])}` монет\n"
                       f"**Уровень:** `{stack['level']}` (опыт: {format_number(stack['exp'])})\n"
                       f"**Заместителей:** {len(await self.bot.pool.fetch('SELECT user_id FROM stack_reviewers WHERE stack_id = $1', stack['stack_id']))}",
            color=COLOR_NEUTRAL
        )
        
        if stack.get('icon_url'):
            embed.set_thumbnail(url=stack['icon_url'])
        
        embed.set_footer(text="Используйте кнопки ниже", icon_url=inter.author.display_avatar.url)
        
        view = ClanSettingsView(self.bot, stack['stack_id'], inter.author.id)
        await inter.edit_original_response(embed=embed, view=view)

    @commands.slash_command(name="казна", description="Пополнить казну клана")
    async def treasury_add(self, inter, сумма: int):
        await inter.response.defer(ephemeral=True)

        if сумма <= 0:
            return await inter.edit_original_response("Сумма должна быть положительной")

        stack = await self.bot.pool.fetchrow(
            "SELECT s.stack_id, s.balance FROM stacks s JOIN stack_members sm ON s.stack_id = sm.stack_id WHERE sm.user_id = $1 AND s.status = 'active'",
            inter.author.id
        )
        if not stack:
            return await inter.edit_original_response("Вы не состоите в стаке")

        balance = await self.bot.pool.fetchval("SELECT balance FROM user_profiles WHERE user_id = $1", inter.author.id) or 0
        if balance < сумма:
            return await inter.edit_original_response(f"Недостаточно средств. У вас {format_number(balance)} монет")

        await self.bot.pool.execute("UPDATE user_profiles SET balance = balance - $1 WHERE user_id = $2", сумма, inter.author.id)
        await self.bot.pool.execute("UPDATE stacks SET balance = balance + $1 WHERE stack_id = $2", сумма, stack['stack_id'])

        await inter.edit_original_response(f"Вы пополнили казну на {format_number(сумма)} монет")

    @commands.slash_command(name="казна_списать", description="Списать деньги с казны клана")
    async def treasury_remove(self, inter, сумма: int):
        await inter.response.defer(ephemeral=True)

        if сумма <= 0:
            return await inter.edit_original_response("Сумма должна быть положительной")

        stack = await self.bot.pool.fetchrow(
            "SELECT s.stack_id, s.balance, s.leader_id FROM stacks s JOIN stack_members sm ON s.stack_id = sm.stack_id WHERE sm.user_id = $1 AND s.status = 'active'",
            inter.author.id
        )
        if not stack:
            return await inter.edit_original_response("Вы не состоите в стаке")

        is_leader = stack['leader_id'] == inter.author.id
        is_deputy = await self.bot.pool.fetchval(
            "SELECT 1 FROM stack_reviewers WHERE stack_id = $1 AND user_id = $2",
            stack['stack_id'], inter.author.id
        )

        if not (is_leader or is_deputy):
            return await inter.edit_original_response("Только лидер или заместитель могут списывать деньги с казны")

        if stack['balance'] < сумма:
            return await inter.edit_original_response(f"Недостаточно средств в казне. В казне {format_number(stack['balance'])} монет")

        await self.bot.pool.execute("UPDATE stacks SET balance = balance - $1 WHERE stack_id = $2", сумма, stack['stack_id'])
        await self.bot.pool.execute("UPDATE user_profiles SET balance = balance + $1 WHERE user_id = $2", сумма, inter.author.id)

        await inter.edit_original_response(f"Вы списали {format_number(сумма)} монет из казны")


def setup(bot):
    bot.add_cog(ClanProfile(bot))