import disnake
from disnake.ext import commands
import time
import sys

sys.path.insert(0, "/root/antisocial")
from shared.config_manager import load_config
from shared.staff import is_staff

active_channels = {}


class SetLimitModal(disnake.ui.Modal):
    def __init__(self, voice_channel):
        self.voice_channel = voice_channel
        components = [disnake.ui.TextInput(label="Лимит участников", placeholder="Пример: 15", custom_id="limit_input", min_length=1, max_length=2, value="0")]
        super().__init__(title="Настройка Канала: Лимит", components=components)

    async def callback(self, interaction):
        await interaction.response.defer(with_message=True, ephemeral=True)
        try:
            limit = int(interaction.text_values["limit_input"])
            if limit < 0 or limit > 99:
                raise ValueError
        except Exception:
            return await interaction.edit_original_response(embed=disnake.Embed(title="—・Ошибка", description="Лимит 0-99.", color=0xF6C4C5))
        try:
            await self.voice_channel.edit(user_limit=limit)
        except Exception:
            return await interaction.edit_original_response(embed=disnake.Embed(title="—・Ошибка", description="Не удалось.", color=0xF6C4C5))
        owner_id = active_channels.get(self.voice_channel.id, {}).get("owner")
        if owner_id:
            await interaction.bot.pool.execute("INSERT INTO private_voice (user_id, user_limit) VALUES ($1, $2) ON CONFLICT (user_id) DO UPDATE SET user_limit = $2", owner_id, limit)
        embed = disnake.Embed(title="—・Управление комнатой", color=0x2b2d31)
        embed.add_field(name="> Действие:", value=f"\u200b**・** Лимит **{limit}** для {self.voice_channel.mention}.", inline=False)
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        await interaction.edit_original_response(embed=embed)


class RenameChannelModal(disnake.ui.Modal):
    cooldowns = {}
    rename_count = {}

    def __init__(self, voice_channel):
        self.voice_channel = voice_channel
        components = [disnake.ui.TextInput(label="Новое название", placeholder="Пример: Игровая", custom_id="new_name_input", min_length=1, max_length=35)]
        super().__init__(title="Настройка Канала: Название", components=components)

    async def callback(self, interaction):
        await interaction.response.defer(with_message=True, ephemeral=True)
        cid = self.voice_channel.id
        now = int(time.time())
        if cid in self.cooldowns and now < self.cooldowns[cid]:
            return await interaction.edit_original_response(embed=disnake.Embed(title="—・Ошибка", description=f"Ждите **<t:{self.cooldowns[cid]}:R>**.", color=0xF6C4C5))
        self.rename_count[cid] = self.rename_count.get(cid, 0) + 1
        if self.rename_count[cid] % 2 == 0:
            self.cooldowns[cid] = now + 600
            return await interaction.edit_original_response(embed=disnake.Embed(title="—・Ошибка", description=f"Ждите **<t:{self.cooldowns[cid]}:R>**.", color=0xF6C4C5))
        new_name = interaction.text_values["new_name_input"]
        try:
            await self.voice_channel.edit(name=new_name)
        except Exception:
            return await interaction.edit_original_response(embed=disnake.Embed(title="—・Ошибка", description="Не удалось.", color=0xF6C4C5))
        owner_id = active_channels.get(self.voice_channel.id, {}).get("owner")
        if owner_id:
            await interaction.bot.pool.execute("INSERT INTO private_voice (user_id, name) VALUES ($1, $2) ON CONFLICT (user_id) DO UPDATE SET name = $2", owner_id, new_name)
        embed = disnake.Embed(title="—・Управление комнатой", color=0x2b2d31)
        embed.add_field(name="> Действие:", value=f"\u200b**・** Имя для {self.voice_channel.mention}.", inline=False)
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        await interaction.edit_original_response(embed=embed)


class MemberSelect(disnake.ui.UserSelect):
    def __init__(self, action, voice_channel, user):
        super().__init__(placeholder="Выберите участника", custom_id=action, min_values=1, max_values=1)
        self.action = action
        self.voice_channel = voice_channel
        self.creator = user

    async def callback(self, interaction):
        await interaction.response.defer(with_message=True, ephemeral=True)
        member = interaction.guild.get_member(self.values[0].id)
        if not member:
            return await interaction.edit_original_response(embed=disnake.Embed(title="—・Ошибка", description="Не найден.", color=0xF6C4C5))
        if member.id == self.creator.id:
            return await interaction.edit_original_response(embed=disnake.Embed(title="—・Ошибка", description="Не к себе.", color=0xF6C4C5))
        if self.action not in ["unban", "ban"] and (member.voice is None or member.voice.channel.id != self.voice_channel.id):
            return await interaction.edit_original_response(embed=disnake.Embed(title="—・Ошибка", description=f"{member.mention} не в {self.voice_channel.mention}!", color=0xF6C4C5))
        uid = self.creator.id
        embed = disnake.Embed(title="—・Управление комнатой", color=0x2b2d31)
        pool = interaction.bot.pool
        await pool.execute("INSERT INTO private_voice (user_id) VALUES ($1) ON CONFLICT DO NOTHING", uid)
        try:
            if self.action == "mute":
                await self.voice_channel.set_permissions(member, speak=False)
                if member.voice:
                    await member.move_to(self.voice_channel)
                await pool.execute("INSERT INTO private_voice_mutes (owner_id, muted_user_id) VALUES ($1, $2) ON CONFLICT DO NOTHING", uid, member.id)
                embed.add_field(name="> Действие:", value=f"\u200b**・** {member.mention} замьючен.", inline=False)
            elif self.action == "unmute":
                await self.voice_channel.set_permissions(member, speak=True)
                if member.voice:
                    await member.move_to(self.voice_channel)
                await pool.execute("DELETE FROM private_voice_mutes WHERE owner_id = $1 AND muted_user_id = $2", uid, member.id)
                embed.add_field(name="> Действие:", value=f"\u200b**・** {member.mention} размьючен.", inline=False)
            elif self.action == "ban":
                await self.voice_channel.set_permissions(member, connect=False)
                if member.voice:
                    await member.move_to(None)
                await pool.execute("INSERT INTO private_voice_bans (owner_id, banned_user_id) VALUES ($1, $2) ON CONFLICT DO NOTHING", uid, member.id)
                embed.add_field(name="> Действие:", value=f"\u200b**・** {member.mention} заблокирован.", inline=False)
            elif self.action == "unban":
                await self.voice_channel.set_permissions(member, connect=True)
                await pool.execute("DELETE FROM private_voice_bans WHERE owner_id = $1 AND banned_user_id = $2", uid, member.id)
                embed.add_field(name="> Действие:", value=f"\u200b**・** {member.mention} разблокирован.", inline=False)
        except Exception as e:
            return await interaction.edit_original_response(embed=disnake.Embed(title="—・Ошибка", description=str(e), color=0xF6C4C5))
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        await interaction.edit_original_response(embed=embed)


class VoiceControlButtons(disnake.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    async def interaction_check(self, interaction):
        passed, retry = await interaction.bot.check_cooldown_redis(interaction.user.id, "voice_ui", 1.5)
        if not passed:
            try:
                await interaction.response.send_message(embed=disnake.Embed(title="⏳ Анти-Спам", description=f"Ждите {retry} сек.", color=0xF8E3A1), ephemeral=True)
            except Exception:
                pass
            return False

        needs_modal = False
        if isinstance(interaction, disnake.MessageInteraction):
            cid = interaction.data.get('custom_id') if interaction.data else None
            if cid in ("rename_channel", "set_limit"):
                needs_modal = True

        vc = interaction.user.voice.channel if interaction.user.voice else None

        if not vc:
            err = disnake.Embed(title="—・Ошибка", description="Вы не в голосовом канале.", color=0xF6C4C5)
            if needs_modal:
                try:
                    await interaction.response.send_message(embed=err, ephemeral=True)
                except Exception:
                    pass
            else:
                try:
                    await interaction.response.send_message(embed=err, ephemeral=True)
                except Exception:
                    pass
            return False

        cfg = await load_config()
        if vc.category_id != cfg.settings.voice_private.category_id:
            err = disnake.Embed(title="—・Ошибка", description="Вы не в приватных комнатах.", color=0xF6C4C5)
            try:
                await interaction.response.send_message(embed=err, ephemeral=True)
            except Exception:
                pass
            return False

        if vc.id not in active_channels or active_channels[vc.id].get("owner") != interaction.user.id:
            err = disnake.Embed(title="—・Ошибка", description=f"Вы не владелец {vc.mention}.", color=0xF6C4C5)
            try:
                await interaction.response.send_message(embed=err, ephemeral=True)
            except Exception:
                pass
            return False

        if not needs_modal:
            try:
                await interaction.response.defer(with_message=True, ephemeral=True)
            except Exception:
                pass

        return True

    @disnake.ui.button(emoji=disnake.PartialEmoji(name="lockserver", id=1344661357676462150), style=disnake.ButtonStyle.secondary, custom_id="close_channel", row=0)
    async def close_channel(self, button, interaction):
        vc = interaction.user.voice.channel
        await vc.set_permissions(interaction.guild.default_role, connect=False)
        await interaction.bot.pool.execute("INSERT INTO private_voice (user_id, is_closed) VALUES ($1, TRUE) ON CONFLICT (user_id) DO UPDATE SET is_closed = TRUE", interaction.user.id)
        embed = disnake.Embed(title="—・Управление комнатой", color=0x2b2d31)
        embed.add_field(name="> Действие:", value=f"\u200b**・** Комната {vc.mention} закрыта.", inline=False)
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        await interaction.edit_original_response(embed=embed)

    @disnake.ui.button(emoji=disnake.PartialEmoji(name="unlockserver", id=1344661368153833543), style=disnake.ButtonStyle.secondary, custom_id="open_channel", row=0)
    async def open_channel(self, button, interaction):
        vc = interaction.user.voice.channel
        await vc.set_permissions(interaction.guild.default_role, connect=True)
        await interaction.bot.pool.execute("INSERT INTO private_voice (user_id, is_closed) VALUES ($1, FALSE) ON CONFLICT (user_id) DO UPDATE SET is_closed = FALSE", interaction.user.id)
        embed = disnake.Embed(title="—・Управление комнатой", color=0x2b2d31)
        embed.add_field(name="> Действие:", value=f"\u200b**・** Комната {vc.mention} открыта.", inline=False)
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        await interaction.edit_original_response(embed=embed)

    @disnake.ui.button(emoji=disnake.PartialEmoji(name="limitserver", id=1344661336180785286), style=disnake.ButtonStyle.secondary, custom_id="set_limit", row=0)
    async def set_limit(self, button, interaction):
        vc = interaction.user.voice.channel if interaction.user.voice else None
        if not vc:
            return await interaction.response.send_message(embed=disnake.Embed(title="—・Ошибка", description="Вы не в канале.", color=0xF6C4C5), ephemeral=True)
        await interaction.response.send_modal(SetLimitModal(vc))

    @disnake.ui.button(emoji=disnake.PartialEmoji(name="renameserver", id=1344661544373452932), style=disnake.ButtonStyle.secondary, custom_id="rename_channel", row=0)
    async def rename_channel(self, button, interaction):
        vc = interaction.user.voice.channel if interaction.user.voice else None
        if not vc:
            return await interaction.response.send_message(embed=disnake.Embed(title="—・Ошибка", description="Вы не в канале.", color=0xF6C4C5), ephemeral=True)
        await interaction.response.send_modal(RenameChannelModal(vc))

    @disnake.ui.button(emoji=disnake.PartialEmoji(name="muteserver", id=1344661427419484272), style=disnake.ButtonStyle.secondary, custom_id="mute_member", row=1)
    async def mute_member(self, button, interaction):
        sv = disnake.ui.View(timeout=300)
        sv.add_item(MemberSelect("mute", interaction.user.voice.channel, interaction.user))
        await interaction.edit_original_response(embed=disnake.Embed(title="—・Мут", color=0x2b2d31), view=sv)

    @disnake.ui.button(emoji=disnake.PartialEmoji(name="nemuteserver", id=1344661412378705980), style=disnake.ButtonStyle.secondary, custom_id="unmute_member", row=1)
    async def unmute_member(self, button, interaction):
        sv = disnake.ui.View(timeout=300)
        sv.add_item(MemberSelect("unmute", interaction.user.voice.channel, interaction.user))
        await interaction.edit_original_response(embed=disnake.Embed(title="—・Размут", color=0x2b2d31), view=sv)

    @disnake.ui.button(emoji=disnake.PartialEmoji(name="nojoinserver", id=1344661496436883551), style=disnake.ButtonStyle.secondary, custom_id="ban_from_channel", row=1)
    async def ban_from_channel(self, button, interaction):
        sv = disnake.ui.View(timeout=300)
        sv.add_item(MemberSelect("ban", interaction.user.voice.channel, interaction.user))
        await interaction.edit_original_response(embed=disnake.Embed(title="—・Бан", color=0x2b2d31), view=sv)

    @disnake.ui.button(emoji=disnake.PartialEmoji(name="viewserver", id=1344661461028438076), style=disnake.ButtonStyle.secondary, custom_id="unban_from_channel", row=1)
    async def unban_from_channel(self, button, interaction):
        sv = disnake.ui.View(timeout=300)
        sv.add_item(MemberSelect("unban", interaction.user.voice.channel, interaction.user))
        await interaction.edit_original_response(embed=disnake.Embed(title="—・Разбан", color=0x2b2d31), view=sv)


class PrivateVoice(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._views_added = False

    @commands.Cog.listener()
    async def on_ready(self):
        if self._views_added:
            return
        self._views_added = True
        self.bot.add_view(VoiceControlButtons())
        cfg = await load_config()
        cat_id = cfg.settings.voice_private.category_id
        trigger_id = cfg.settings.voice_private.trigger_channel_id
        if not cat_id or not trigger_id:
            return
        pool = self.bot.pool
        for guild in self.bot.guilds:
            category = guild.get_channel(cat_id)
            if not category:
                continue
            for channel in category.voice_channels:
                if channel.id == trigger_id:
                    continue
                if len(channel.members) == 0:
                    try:
                        await channel.delete()
                    except Exception:
                        pass
                    await pool.execute("DELETE FROM active_voice_channels WHERE channel_id = $1", channel.id)
                else:
                    owner_id = await pool.fetchval("SELECT owner_id FROM active_voice_channels WHERE channel_id = $1", channel.id)
                    if owner_id:
                        active_channels[channel.id] = {"voice": channel.id, "owner": owner_id}
                    else:
                        active_channels[channel.id] = {"voice": channel.id, "owner": channel.members[0].id}
                        await pool.execute("INSERT INTO active_voice_channels (channel_id, owner_id) VALUES ($1, $2) ON CONFLICT (channel_id) DO UPDATE SET owner_id = $2", channel.id, channel.members[0].id)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        cfg = await load_config()
        trigger_id = cfg.settings.voice_private.trigger_channel_id
        cat_id = cfg.settings.voice_private.category_id
        pool = self.bot.pool
        if after.channel and after.channel.id == trigger_id:
            guild = member.guild
            category = guild.get_channel(cat_id) if cat_id else after.channel.category
            row = await pool.fetchrow("SELECT name, user_limit, is_closed FROM private_voice WHERE user_id = $1", member.id)
            ch_name = row['name'] if row and row['name'] else member.display_name
            ch_limit = row['user_limit'] if row and row['user_limit'] else 0
            is_closed = bool(row['is_closed']) if row else False
            b_rows = await pool.fetch("SELECT banned_user_id FROM private_voice_bans WHERE owner_id = $1", member.id)
            banned = [r['banned_user_id'] for r in b_rows]
            m_rows = await pool.fetch("SELECT muted_user_id FROM private_voice_mutes WHERE owner_id = $1", member.id)
            muted = [r['muted_user_id'] for r in m_rows]
            overwrites = {
                guild.default_role: disnake.PermissionOverwrite(connect=not is_closed, view_channel=True),
                member: disnake.PermissionOverwrite(connect=True, view_channel=True)
            }
            for b_id in banned:
                overwrites[disnake.Object(id=b_id)] = disnake.PermissionOverwrite(connect=False)
            for m_id in muted:
                overwrites[disnake.Object(id=m_id)] = disnake.PermissionOverwrite(speak=False)
            try:
                new_channel = await guild.create_voice_channel(name=ch_name, category=category, user_limit=ch_limit, overwrites=overwrites)
                await member.move_to(new_channel)
                active_channels[new_channel.id] = {"voice": new_channel.id, "owner": member.id}
                await pool.execute("INSERT INTO active_voice_channels (channel_id, owner_id) VALUES ($1, $2) ON CONFLICT (channel_id) DO UPDATE SET owner_id = $2", new_channel.id, member.id)
            except Exception:
                pass
        if before.channel and before.channel.id in active_channels:
            if len(before.channel.members) == 0:
                try:
                    await before.channel.delete()
                except Exception:
                    pass
                await pool.execute("DELETE FROM active_voice_channels WHERE channel_id = $1", before.channel.id)
                active_channels.pop(before.channel.id, None)

    @commands.slash_command(description="Отправка панели для настройки временных войсов.")
    async def отправка_панели(self, inter):
        if not await is_staff(inter.author.id):
            return await inter.response.send_message(embed=disnake.Embed(title="—・Ошибка", description="Доступ запрещен.", color=0xF6C4C5), ephemeral=True)
        await inter.response.defer(ephemeral=True, with_message=True)
        embed = disnake.Embed(title="—・Управление Приватными Комнатами", description="Используйте кнопки ниже.", color=0x2b2d31)
        embed.add_field(name="> Основные настройки:", value="\u200b**・** <:unlockserver:1477010525106999446> — Открыть\n\u200b**・** <:lockserver:1477010396597457079> — Закрыть\n\u200b**・** <:limitserver:1477010370307686574> — Лимит\n\u200b**・** <:renameserver:1477010467162423409> — Переименовать", inline=True)
        embed.add_field(name="> Управление доступом:", value="\u200b**・** <:muteserver:1477010414217990317> — Мут\n\u200b**・** <:nemuteserver:1477010432979112118> — Размут\n\u200b**・** <:nojoinserver:1477010449475047485> — Бан\n\u200b**・** <:viewserver:1477010553405833278> — Разбан", inline=True)
        if inter.guild.icon:
            embed.set_thumbnail(url=inter.guild.icon.url)
        await inter.channel.send(embed=embed, view=VoiceControlButtons())
        await inter.edit_original_response(embed=disnake.Embed(title="—・Успешно", description="Панель отправлена.", color=0x9EE5B4))


def setup(bot):
    bot.add_cog(PrivateVoice(bot))
