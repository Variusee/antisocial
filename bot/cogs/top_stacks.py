import disnake
from disnake.ext import commands
import sys

sys.path.insert(0, "/root/antisocial")

PER_PAGE = 10
COLOR_NEUTRAL = 0x2b2d31


def _format_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}с"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}м"
    hours = minutes // 60
    rem_minutes = minutes % 60
    if hours < 24:
        return f"{hours}ч {rem_minutes}м" if rem_minutes else f"{hours}ч"
    days = hours // 24
    rem_hours = hours % 24
    return f"{days}д {rem_hours}ч" if rem_hours else f"{days}д"


def _medal(idx: int) -> str:
    return {0: "🥇", 1: "🥈", 2: "🥉"}.get(idx, f"#{idx + 1}")


async def _build_top_data(pool):
    rows = await pool.fetch(
        """
        SELECT
            s.stack_id,
            s.stack_name,
            s.leader_id,
            COALESCE(SUM(svh.seconds), 0) AS total_seconds,
            (SELECT COUNT(*) FROM stack_members sm WHERE sm.stack_id = s.stack_id) AS members_count
        FROM stacks s
        LEFT JOIN stack_voice_hours svh ON svh.stack_id = s.stack_id
        WHERE s.status = 'active'
        GROUP BY s.stack_id, s.stack_name, s.leader_id
        ORDER BY total_seconds DESC, s.stack_id ASC
        """
    )
    return [dict(r) for r in rows]


def _build_embed(rows, page: int, total_pages: int):
    embed = disnake.Embed(
        title="🏆  Топ стаков по часам в войсе",
        description=(
            f"Активных стаков: **{len(rows)}**\n"
            f"Страница: **{page + 1} / {total_pages}**\n"
            f"_Учитывается суммарное время участников стака в его голосовых каналах._"
        ),
        color=COLOR_NEUTRAL
    )
    start = page * PER_PAGE
    end = start + PER_PAGE
    page_rows = rows[start:end]

    if not page_rows:
        embed.add_field(name="Пусто", value="Нет данных за этот период.", inline=False)
        return embed

    for i, r in enumerate(page_rows, start=start):
        medal = _medal(i)
        time_str = _format_duration(int(r["total_seconds"]))
        embed.add_field(
            name=f"{medal}  {r['stack_name']}",
            value=(
                f"\u200b**・** Часы: **{time_str}**\n"
                f"\u200b**・** Лидер: <@{r['leader_id']}>\n"
                f"\u200b**・** Участников: **{r['members_count']}**\n"
                f"\u200b**・** ID: `#{r['stack_id']:04d}`"
            ),
            inline=False
        )
    return embed


class TopStacksView(disnake.ui.View):
    def __init__(self, bot, invoker_id, rows):
        super().__init__(timeout=300)
        self.bot = bot
        self.invoker_id = invoker_id
        self.rows = rows
        self.page = 0
        self._rebuild()

    async def interaction_check(self, inter):
        return inter.author.id == self.invoker_id

    def _total_pages(self):
        if not self.rows:
            return 1
        return (len(self.rows) - 1) // PER_PAGE + 1

    def _rebuild(self):
        self.clear_items()
        total_pages = self._total_pages()
        if self.page >= total_pages:
            self.page = total_pages - 1
        if self.page < 0:
            self.page = 0

        if total_pages > 1:
            prev_b = disnake.ui.Button(label="◀", style=disnake.ButtonStyle.secondary, disabled=self.page == 0, row=0)
            prev_b.callback = self._prev
            self.add_item(prev_b)
            page_b = disnake.ui.Button(label=f"Стр. {self.page + 1}/{total_pages}", style=disnake.ButtonStyle.grey, disabled=True, row=0)
            self.add_item(page_b)
            next_b = disnake.ui.Button(label="▶", style=disnake.ButtonStyle.secondary, disabled=self.page >= total_pages - 1, row=0)
            next_b.callback = self._next
            self.add_item(next_b)

        close_b = disnake.ui.Button(label="Закрыть", style=disnake.ButtonStyle.grey, emoji="✖️", row=1)
        close_b.callback = self._close
        self.add_item(close_b)

    async def _prev(self, inter):
        self.page -= 1
        self._rebuild()
        await inter.response.edit_message(embed=_build_embed(self.rows, self.page, self._total_pages()), view=self)

    async def _next(self, inter):
        self.page += 1
        self._rebuild()
        await inter.response.edit_message(embed=_build_embed(self.rows, self.page, self._total_pages()), view=self)

    async def _close(self, inter):
        await inter.response.edit_message(
            embed=disnake.Embed(title="Топ закрыт", color=COLOR_NEUTRAL),
            view=None
        )


class TopStacks(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.slash_command(name="топ_стаков", description="Рейтинг стаков по часам в голосовых")
    async def top_stacks(self, inter):
        await inter.response.defer(ephemeral=True, with_message=True)
        rows = await _build_top_data(self.bot.pool)
        if not rows:
            return await inter.edit_original_response(
                embed=disnake.Embed(title="Топ пуст", description="Нет активных стаков.", color=COLOR_NEUTRAL)
            )
        view = TopStacksView(self.bot, inter.author.id, rows)
        embed = _build_embed(rows, 0, view._total_pages())
        await inter.edit_original_response(embed=embed, view=view)


def setup(bot):
    bot.add_cog(TopStacks(bot))
