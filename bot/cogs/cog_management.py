import disnake
from disnake.ext import commands
import sys
import traceback

sys.path.insert(0, "/root/antisocial")

from shared.staff import is_staff


COLOR_NEUTRAL = 0x2b2d31
COLOR_OK = 0x9EE5B4
COLOR_ERR = 0xF6C4C5
COLOR_WARN = 0xF8E3A1


def _container(*children, color: int) -> disnake.ui.Container:
    c = disnake.ui.Container(*children)
    try:
        c.accent_colour = disnake.Colour(color)
    except Exception:
        pass
    return c


def err_container(desc: str) -> disnake.ui.Container:
    return _container(
        disnake.ui.TextDisplay(f"# —・Ошибка\n\n{desc}"),
        color=COLOR_ERR
    )


def ok_container(title: str, desc: str) -> disnake.ui.Container:
    return _container(
        disnake.ui.TextDisplay(f"# —・{title}\n\n{desc}"),
        color=COLOR_OK
    )


def _cog_label(name: str) -> str:
    labels = {
        "clan_tag": "🏷️ Клан-тег",
        "giveaway": "🎁 Розыгрыши",
        "logger": "📋 Логирование",
        "panel": "🎛️ Панель /бот",
        "settings": "⚙️ Настройки",
        "stacks": "📦 Стаки",
        "stack_tag_check": "🔖 Проверка тегов в стаках",
        "status": "📊 Статус",
        "top_stacks": "🏆 Топ стаков",
        "voice_hours_tracker": "🎙️ Учёт часов (трекер)",
        "voice_stats": "📈 Часы и лидерборд",
        "voice_tracker": "🎤 Войс-трекер стаков",
        "voiceprivate": "🔒 Приватные войсы",
        "cog_management": "🧩 Управление когами",
    }
    return labels.get(name, f"⚙️ {name}")


def _list_loaded_cog_names(bot) -> list[str]:
    names = []
    for ext_name in list(bot.extensions.keys()):
        if ext_name.startswith("cogs."):
            names.append(ext_name[len("cogs."):])
    return sorted(names)


def _build_panel(bot) -> list:
    loaded = _list_loaded_cog_names(bot)
    if not loaded:
        text = "## 🧩 Управление когами\n\nКоги не загружены."
    else:
        lines = ["## 🧩 Управление когами\n", f"**Загружено когов:** {len(loaded)}\n"]
        for n in loaded:
            lines.append(f"\u200b**・** `{n}` — {_cog_label(n)}")
        text = "\n".join(lines)

    container = _container(
        disnake.ui.TextDisplay(text),
        color=COLOR_NEUTRAL
    )
    return [container]


class CogManagement(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _ensure_super_admin(self, inter) -> bool:
        if not await is_staff(inter.author.id):
            await inter.edit_original_response(
                components=[err_container("Команда доступна только супер-админам.")]
            )
            return False
        return True

    @commands.slash_command(
        name="коги",
        description="Список загруженных когов"
    )
    async def list_cogs(self, inter):
        await inter.response.defer(ephemeral=True, with_message=True)
        if not await self._ensure_super_admin(inter):
            return
        await inter.edit_original_response(components=_build_panel(self.bot))

    @commands.slash_command(
        name="перезагрузить",
        description="Перезагрузить ког или все коги сразу"
    )
    async def reload_cog(
        self,
        inter,
        ког: str = commands.Param(
            default="",
            description="Имя кога (пусто — перезагрузить все)"
        )
    ):
        await inter.response.defer(ephemeral=True, with_message=True)
        if not await self._ensure_super_admin(inter):
            return

        bot = self.bot
        results = []

        if not ког.strip():
            target_names = _list_loaded_cog_names(bot)
            if not target_names:
                return await inter.edit_original_response(
                    components=[err_container("Нет загруженных когов.")]
                )
            for name in target_names:
                ext = f"cogs.{name}"
                try:
                    bot.reload_extension(ext)
                    results.append(f"✅  `{name}`")
                except Exception as e:
                    results.append(f"❌  `{name}` — `{type(e).__name__}: {e}`")
                    traceback.print_exc()

            text = "## ♻️ Перезагрузка всех когов\n\n" + "\n".join(results)
            container = _container(
                disnake.ui.TextDisplay(text),
                color=COLOR_OK if all("✅" in r for r in results) else COLOR_WARN
            )
            return await inter.edit_original_response(components=[container])

        name = ког.strip()
        ext = f"cogs.{name}"
        if ext not in bot.extensions:
            return await inter.edit_original_response(
                components=[err_container(f"Ког `{name}` не загружен. Используйте `/загрузить`.")]
            )
        try:
            bot.reload_extension(ext)
            await inter.edit_original_response(components=[ok_container(
                "Ког перезагружен",
                f"Ког `{name}` ({_cog_label(name)}) перезагружен."
            )])
        except Exception as e:
            traceback.print_exc()
            await inter.edit_original_response(components=[err_container(
                f"Не удалось перезагрузить `{name}`:\n```\n{type(e).__name__}: {e}\n```"
            )])

    @reload_cog.autocomplete("ког")
    async def reload_autocomplete(self, inter, current: str):
        loaded = _list_loaded_cog_names(self.bot)
        cur = current.lower()
        filtered = [n for n in loaded if cur in n.lower()][:25]
        return filtered

    @commands.slash_command(
        name="выгрузить",
        description="Выгрузить ког"
    )
    async def unload_cog(
        self,
        inter,
        ког: str = commands.Param(description="Имя кога")
    ):
        await inter.response.defer(ephemeral=True, with_message=True)
        if not await self._ensure_super_admin(inter):
            return

        bot = self.bot
        name = ког.strip()
        ext = f"cogs.{name}"

        if name == "cog_management":
            return await inter.edit_original_response(components=[err_container(
                "Нельзя выгрузить `cog_management` — иначе пропадут команды управления."
            )])

        if ext not in bot.extensions:
            return await inter.edit_original_response(components=[err_container(
                f"Ког `{name}` уже не загружен."
            )])

        try:
            bot.unload_extension(ext)
            await inter.edit_original_response(components=[ok_container(
                "Ког выгружен",
                f"Ког `{name}` ({_cog_label(name)}) выгружен."
            )])
        except Exception as e:
            traceback.print_exc()
            await inter.edit_original_response(components=[err_container(
                f"Не удалось выгрузить `{name}`:\n```\n{type(e).__name__}: {e}\n```"
            )])

    @unload_cog.autocomplete("ког")
    async def unload_autocomplete(self, inter, current: str):
        loaded = _list_loaded_cog_names(self.bot)
        cur = current.lower()
        filtered = [n for n in loaded if cur in n.lower() and n != "cog_management"][:25]
        return filtered

    @commands.slash_command(
        name="загрузить",
        description="Загрузить ког"
    )
    async def load_cog(
        self,
        inter,
        ког: str = commands.Param(description="Имя кога")
    ):
        await inter.response.defer(ephemeral=True, with_message=True)
        if not await self._ensure_super_admin(inter):
            return

        bot = self.bot
        name = ког.strip()
        ext = f"cogs.{name}"

        if ext in bot.extensions:
            return await inter.edit_original_response(components=[err_container(
                f"Ког `{name}` уже загружен. Используйте `/перезагрузить`."
            )])

        try:
            bot.load_extension(ext)
            await inter.edit_original_response(components=[ok_container(
                "Ког загружен",
                f"Ког `{name}` ({_cog_label(name)}) загружен."
            )])
        except Exception as e:
            traceback.print_exc()
            await inter.edit_original_response(components=[err_container(
                f"Не удалось загрузить `{name}`:\n```\n{type(e).__name__}: {e}\n```"
            )])

    @load_cog.autocomplete("ког")
    async def load_autocomplete(self, inter, current: str):
        from pathlib import Path
        loaded = set(_list_loaded_cog_names(self.bot))
        cogs_dir = Path("/root/antisocial/bot/cogs")
        all_files = []
        if cogs_dir.exists():
            for f in cogs_dir.glob("*.py"):
                if f.stem.startswith("_"):
                    continue
                if f.stem not in loaded:
                    all_files.append(f.stem)
        cur = current.lower()
        return [n for n in sorted(all_files) if cur in n.lower()][:25]


def setup(bot):
    bot.add_cog(CogManagement(bot))
