import os
import traceback
import disnake


async def send_error_log(bot, error: BaseException, context: str = "", bot_name: str = None):
    if error is None:
        return
    ch_id = os.getenv("ERROR_LOG_CHANNEL_ID", "").strip()
    if not ch_id.isdigit():
        print(f"[error_log] (нет канала) {context}: {type(error).__name__}: {error}")
        traceback.print_exc()
        return
    try:
        ch = bot.get_channel(int(ch_id)) or await bot.fetch_channel(int(ch_id))
        if not ch:
            return
        tb = "".join(traceback.format_exception(type(error), error, error.__traceback__))
        if len(tb) > 1900:
            tb = tb[:1900] + "...[truncated]"
        if bot_name is None:
            bot_name = getattr(bot, "bot_name", "?")
        embed = disnake.Embed(
            title=f"—・⚠️ Ошибка — {bot_name.upper()}",
            description=f"**Контекст:** {context or '—'}\n```py\n{tb}\n```",
            color=0xF6C4C5,
            timestamp=disnake.utils.utcnow()
        )
        await ch.send(embed=embed)
    except Exception:
        print(f"[error_log] Не смог отправить traceback в канал: {context}")
        print(traceback.format_exc())
