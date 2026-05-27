import disnake
from disnake.ext import commands
from datetime import datetime, timezone, timedelta
import sys

sys.path.insert(0, "/root/antisocial")
from shared.config_manager import load_config

PER_PAGE = 10
COLOR_NEUTRAL = 0x2b2d31
COLOR_OK = 0x9EE5B4
COLOR_ERR = 0xF6C4C5

PERIOD_LABELS = {
    "all": "Всё время",
    "month": "Последние 30 дней",
    "week": "Последние 7 дней",
    "day": "Сегодня",
}


def _format_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} сек"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} мин"
    hours = minutes // 60
    rem_min = minutes % 60
    if hours < 24:
        return f"{hours}ч {rem_min}м" if rem_min else f"{hours}ч"
    days = hours // 24
    rem_h = hours % 24
    return f"{days}д {rem_h}ч" if rem_h else f"{days}д"


def _medal(idx: int) -> str:
    return {0: "🥇", 1: "🥈", 2: "🥉"}.get(idx, f"#{idx + 1}")


def _period_filter(period: str):
    today_utc = datetime.now(tz=timezone.utc).date()
    if period == "all":
        return "", []
    if period == "day":
        return "AND day_key = $X", [today_utc.strftime("%Y-%m-%d")]
    if period == "week":
        start = (today_utc - timedelta(days=6)).strftime("%Y-%m-%d")
        return "AND day_key >= $X", [start]
    if period == "month":
        start = (today_utc - timedelta(days=29)).strftime("%Y-%m-%d")
        return "AND day_key >= $X", [start]
    return "", []


RESET_KEYWORD = "СБРОСИТЬ"


class ResetAllStatsModal(disnake.ui.Modal):
    def __init__(self):
        components = [
            disnake.ui.TextInput(
                label="Введите СБРОСИТЬ заглавными буквами",
                placeholder="СБРОСИТЬ",
                custom_id="confirm",
                style=disnake.TextInputStyle.short,
                required=True,
                max_length=20,
            )
        ]
        super().__init__(
            title="Подтверждение сброса",
            custom_id="reset_all_stats_modal",
            components=components,
            timeout=120,
        )

    async def callback(self, inter):
        await inter.response.defer(ephemeral=True, with_message=True)
        cog = inter.bot.get_cog("VoiceStats")
        if not cog:
            return await inter.edit_original_response(
                embed=disnake.Embed(title="—・Ошибка", description="Cog не загружен.", color=COLOR_ERR)
            )
        text = inter.text_values.get("confirm", "").strip()
        if text != RESET_KEYWORD:
            return await inter.edit_original_response(embed=disnake.Embed(
                title="—・Отменено",
                description=f"Введённое значение не совпадает с **{RESET_KEYWORD}**. Сброс отменён.",
                color=COLOR_NEUTRAL
            ))
        try:
            deleted = await cog.bot.pool.fetchval(
                "WITH d AS (DELETE FROM voice_hours WHERE guild_id = $1 RETURNING 1) SELECT COUNT(*) FROM d",
                inter.guild.id
            )
            await inter.edit_original_response(embed=disnake.Embed(
                title="—・Готово",
                description=(
                    f"Статистика голоса полностью сброшена.\n"
                    f"\u200b**・** Удалено записей: **{deleted}**"
                ),
                color=COLOR_OK
            ))
            print(f"[VoiceStats] /сбросить_всю_статистику от {inter.author} ({inter.author.id}): удалено {deleted} записей", flush=True)
        except Exception as e:
            await inter.edit_original_response(embed=disnake.Embed(
                title="—・Ошибка",
                description=f"Не удалось сбросить: ```{str(e)[:300]}```",
                color=COLOR_ERR
            ))


class VoiceStats(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.slash_command(name="часы", description="Часы в голосовых каналах")
    async def hours(
        self,
        inter,
        участник: disnake.Member = None,
        период: str = commands.Param(
            default="all",
            choices=[
                disnake.OptionChoice(name="Всё время", value="all"),
                disnake.OptionChoice(name="За месяц", value="month"),
                disnake.OptionChoice(name="За неделю", value="week"),
                disnake.OptionChoice(name="За день", value="day"),
            ]
        )
    ):
        await inter.response.defer(ephemeral=True, with_message=True)
        target = участник or inter.author

        cond, params = _period_filter(период)
        sql = f"""
            SELECT COALESCE(SUM(seconds), 0) AS total
            FROM voice_hours
            WHERE guild_id = $1 AND user_id = $2
            { cond.replace('$X', f'${len(params) + 2}') if cond else '' }
        """
        args = [inter.guild.id, target.id, *params]
        row = await self.bot.pool.fetchrow(sql, *args)
        total_seconds = int(row['total']) if row else 0

        rank_sql = f"""
            SELECT user_id, SUM(seconds) AS total
            FROM voice_hours
            WHERE guild_id = $1
            { cond.replace('$X', f'${len(params) + 1}') if cond else '' }
            GROUP BY user_id
            ORDER BY total DESC
        """
        rank_args = [inter.guild.id, *params]
        rows = await self.bot.pool.fetch(rank_sql, *rank_args)
        rank = None
        for i, r in enumerate(rows):
            if r['user_id'] == target.id:
                rank = i + 1
                break

        embed = disnake.Embed(
            title=f"⏱️  Статистика — {target.display_name}",
            description=f"_Период: **{PERIOD_LABELS[период]}**_",
            color=COLOR_NEUTRAL
        )
        embed.add_field(
            name="> Время в войсе",
            value=f"\u200b**・** **{_format_duration(total_seconds)}**" if total_seconds else "\u200b**・** *нет данных*",
            inline=True
        )
        if rank is not None:
            embed.add_field(
                name="> Место в топе",
                value=f"\u200b**・** **#{rank}** из **{len(rows)}**",
                inline=True
            )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.set_footer(text=f"ID: {target.id}")
        await inter.edit_original_response(embed=embed)

    @commands.slash_command(name="лидерборд", description="Топ участников по часам в войсе")
    async def leaderboard(
        self,
        inter,
        период: str = commands.Param(
            default="all",
            choices=[
                disnake.OptionChoice(name="Всё время", value="all"),
                disnake.OptionChoice(name="За месяц", value="month"),
                disnake.OptionChoice(name="За неделю", value="week"),
                disnake.OptionChoice(name="За день", value="day"),
            ]
        )
    ):
        await inter.response.defer(ephemeral=True, with_message=True)

        cond, params = _period_filter(период)
        sql = f"""
            SELECT user_id, SUM(seconds) AS total
            FROM voice_hours
            WHERE guild_id = $1
            { cond.replace('$X', f'${len(params) + 1}') if cond else '' }
            GROUP BY user_id
            HAVING SUM(seconds) > 0
            ORDER BY total DESC
        """
        args = [inter.guild.id, *params]
        rows = await self.bot.pool.fetch(sql, *args)
        rows = [{"user_id": r['user_id'], "total": int(r['total'])} for r in rows]

        if not rows:
            return await inter.edit_original_response(
                embed=disnake.Embed(title="Топ пуст", description="Нет данных за этот период.", color=COLOR_NEUTRAL)
            )

        view = LeaderboardView(self.bot, inter.author.id, rows, период)
        embed = view._build_embed()
        await inter.edit_original_response(embed=embed, view=view)

    @commands.slash_command(
        name="сбросить_всю_статистику",
        description="ПОЛНОСТЬЮ удалить всю статистику часов в голосе"
    )
    async def reset_all_stats(self, inter):
        cfg = await load_config()
        if inter.author.id != cfg.settings.super_admin_id:
            return await inter.response.send_message(
                embed=disnake.Embed(
                    title="—・Ошибка",
                    description="Только **супер-админ** может выполнить эту команду.",
                    color=COLOR_ERR
                ),
                ephemeral=True
            )
        await inter.response.send_modal(ResetAllStatsModal())


class LeaderboardView(disnake.ui.View):
    def __init__(self, bot, invoker_id, rows, period):
        super().__init__(timeout=300)
        self.bot = bot
        self.invoker_id = invoker_id
        self.rows = rows
        self.period = period
        self.page = 0
        self._rebuild()

    async def interaction_check(self, inter):
        return inter.author.id == self.invoker_id

    def _total_pages(self):
        if not self.rows:
            return 1
        return (len(self.rows) - 1) // PER_PAGE + 1

    def _build_embed(self):
        total_pages = self._total_pages()
        embed = disnake.Embed(
            title="🏆  Лидерборд — часы в войсе",
            description=(
                f"_Период: **{PERIOD_LABELS[self.period]}**_\n"
                f"Участников в топе: **{len(self.rows)}**\n"
                f"Страница: **{self.page + 1}/{total_pages}**"
            ),
            color=COLOR_NEUTRAL
        )
        start = self.page * PER_PAGE
        end = start + PER_PAGE
        lines = []
        for i, r in enumerate(self.rows[start:end], start=start):
            lines.append(
                f"{_medal(i)} <@{r['user_id']}> — **{_format_duration(r['total'])}**"
            )
        if lines:
            embed.add_field(
                name="\u200b",
                value="\n".join(lines),
                inline=False
            )
        return embed

    def _rebuild(self):
        self.clear_items()
        total_pages = self._total_pages()
        if self.page >= total_pages:
            self.page = total_pages - 1
        if self.page < 0:
            self.page = 0
        if total_pages > 1:
            prev_b = disnake.ui.Button(label="◀", style=disnake.ButtonStyle.secondary, row=0, disabled=self.page == 0)
            prev_b.callback = self._prev
            self.add_item(prev_b)
            page_b = disnake.ui.Button(
                label=f"Стр. {self.page + 1}/{total_pages}",
                style=disnake.ButtonStyle.grey,
                disabled=True,
                row=0
            )
            self.add_item(page_b)
            next_b = disnake.ui.Button(
                label="▶",
                style=disnake.ButtonStyle.secondary,
                row=0,
                disabled=self.page >= total_pages - 1
            )
            next_b.callback = self._next
            self.add_item(next_b)

    async def _prev(self, inter):
        self.page -= 1
        self._rebuild()
        await inter.response.edit_message(embed=self._build_embed(), view=self)

    async def _next(self, inter):
        self.page += 1
        self._rebuild()
        await inter.response.edit_message(embed=self._build_embed(), view=self)


def setup(bot):
    bot.add_cog(VoiceStats(bot))
