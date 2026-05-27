import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import asyncio
import os

# FIX для Windows: принудительно используем SelectorEventLoop
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# FIX для ошибки _ready в disnake
import disnake
from disnake.ext import commands

# Патч для исправления _ready
original_Client = disnake.client.Client

class PatchedClient(original_Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._ready = asyncio.Event()

disnake.client.Client = PatchedClient

from pathlib import Path

BOT_NAME = "antisocial"
PROJECT_ROOT_STR = str(Path(__file__).parent.parent)
USE_INTERACTION_BOT = True

PROJECT_ROOT = Path(PROJECT_ROOT_STR)
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from shared.database import init_pool, close_pool
from shared.redis_client import init_redis, close_redis
from shared.error_log import send_error_log
from shared.staff import is_staff

try:
    from shared.config_manager import start_config_listener
except ImportError:
    start_config_listener = None
try:
    from shared.config_manager import start_listener as _legacy_start_listener
    from shared.config_manager import stop_listener as _legacy_stop_listener
except ImportError:
    _legacy_start_listener = None
    _legacy_stop_listener = None


intents = disnake.Intents.default()
intents.members = True
intents.message_content = False
intents.voice_states = True
intents.presences = True

if USE_INTERACTION_BOT:
    bot = commands.InteractionBot(
        intents=intents,
        command_sync_flags=commands.CommandSyncFlags.none(),
    )
else:
    bot = commands.Bot(
        command_prefix="!",
        intents=intents,
        help_command=None,
        command_sync_flags=commands.CommandSyncFlags.none(),
    )

bot.bot_name = BOT_NAME
bot.pool = None
bot.redis = None
bot._ready = asyncio.Event()  # Явно создаём Event


async def check_cooldown_redis(user_id: int, action: str, seconds: float = 1.5):
    if not bot.redis:
        return True, 0
    key = f"cd:{BOT_NAME}:{action}:{user_id}"
    try:
        if await bot.redis.exists(key):
            ttl = await bot.redis.ttl(key)
            return False, max(1, ttl)
        await bot.redis.set(key, "1", ex=int(seconds) if seconds >= 1 else 1)
        return True, 0
    except Exception:
        return True, 0


bot.check_cooldown_redis = check_cooldown_redis


async def clear_all_commands(guild_id: int):
    try:
        app_info = await bot.application_info()
        
        route = disnake.http.Route(
            "PUT",
            "/applications/{app_id}/commands",
            app_id=app_info.id,
        )
        await bot.http.request(route, json=[])
        print(f"[{BOT_NAME.upper()}] ✅ Глобальные команды очищены", flush=True)
        
        if guild_id:
            route = disnake.http.Route(
                "PUT",
                "/applications/{app_id}/guilds/{guild_id}/commands",
                app_id=app_info.id,
                guild_id=guild_id,
            )
            await bot.http.request(route, json=[])
            print(f"[{BOT_NAME.upper()}] ✅ Команды сервера {guild_id} очищены", flush=True)
            
    except Exception as e:
        print(f"[{BOT_NAME.upper()}] ⚠️ Ошибка очистки команд: {e}", flush=True)


async def sync_commands(guild_id: int):
    try:
        all_cmds = bot.application_commands
        if not all_cmds:
            print(f"[{BOT_NAME.upper()}] ⚠️ Нет команд для синхронизации", flush=True)
            return
        
        payload = []
        for cmd in all_cmds:
            try:
                body = cmd.body
                if hasattr(body, 'to_dict'):
                    payload.append(body.to_dict())
                elif isinstance(body, dict):
                    payload.append(body)
            except Exception as e:
                print(f"   ⚠️ Не удалось сериализовать /{cmd.name}: {e}", flush=True)
        
        if not payload:
            return
        
        app_info = await bot.application_info()
        route = disnake.http.Route(
            "PUT",
            "/applications/{app_id}/guilds/{guild_id}/commands",
            app_id=app_info.id,
            guild_id=guild_id,
        )
        result = await bot.http.request(route, json=payload)
        print(f"[{BOT_NAME.upper()}] ✅ Discord принял {len(result)} команд", flush=True)
        
        for cmd in result:
            print(f"   ✅ /{cmd['name']}", flush=True)
            
    except Exception as e:
        import traceback
        print(f"[{BOT_NAME.upper()}] ❌ Sync error: {e}", flush=True)
        traceback.print_exc()


@bot.event
async def on_ready():
    print(f"[{BOT_NAME.upper()}] {bot.user} готов. Гильдий: {len(bot.guilds)}", flush=True)

    all_cmds = bot.application_commands
    print(f"[{BOT_NAME.upper()}] Зарегистрировано команд в боте: {len(all_cmds)}", flush=True)
    for cmd in all_cmds:
        print(f"   - /{cmd.name}", flush=True)

    test_guild = os.getenv("TEST_GUILD_ID", "").strip()
    if not test_guild.isdigit():
        print(f"[{BOT_NAME.upper()}] TEST_GUILD_ID не задан — sync пропущен", flush=True)
        return
    
    gid = int(test_guild)
    
    print(f"[{BOT_NAME.upper()}] 🗑️ Очищаем старые команды...", flush=True)
    await clear_all_commands(gid)
    
    await asyncio.sleep(2)
    
    print(f"[{BOT_NAME.upper()}] 🔄 Синхронизация новых команд...", flush=True)
    await sync_commands(gid)
    
    bot._ready.set()


@bot.event
async def on_slash_command_error(inter, error):
    if isinstance(error, (commands.MissingPermissions, disnake.Forbidden)):
        try:
            if not inter.response.is_done():
                await inter.response.send_message(
                    embed=disnake.Embed(title="—・Ошибка", description="Нет прав.", color=0xF6C4C5),
                    ephemeral=True
                )
        except Exception:
            pass
        return
    if isinstance(error, commands.CommandOnCooldown):
        try:
            if not inter.response.is_done():
                await inter.response.send_message(
                    embed=disnake.Embed(title="—・Спам", description=f"Подождите {round(error.retry_after, 1)} сек.", color=0xF8E3A1),
                    ephemeral=True
                )
        except Exception:
            pass
        return
    try:
        if not inter.response.is_done():
            await inter.response.send_message(
                embed=disnake.Embed(title="—・Системная ошибка", description="Произошла ошибка. Разработчик уведомлен.", color=0xF6C4C5),
                ephemeral=True
            )
    except Exception:
        pass
    try:
        cmd_name = inter.application_command.name if inter.application_command else "?"
        await send_error_log(bot, error, f"Команда: /{cmd_name} | От: {getattr(inter.author, 'name', '?')}")
    except Exception:
        pass


@bot.event
async def on_error(event_method: str, *args, **kwargs):
    error = sys.exc_info()[1]
    if error is not None:
        try:
            await send_error_log(bot, error, f"Ивент: {event_method}")
        except Exception:
            pass


def _list_cog_files():
    cogs_dir = Path(__file__).parent / "cogs"
    if not cogs_dir.exists():
        return []
    return sorted([
        fn.stem for fn in cogs_dir.glob("*.py")
        if not fn.name.startswith("_")
    ])


def _is_loaded(cog_name: str) -> bool:
    return any(ext.endswith(f".{cog_name}") for ext in bot.extensions.keys())


@bot.slash_command(name="cogs", description="Список всех cogs и их статус")
async def _cogs_list(inter):
    if not await is_staff(inter.author.id):
        return await inter.response.send_message(
            embed=disnake.Embed(title="—・Ошибка", description="Доступ запрещён.", color=0xF6C4C5),
            ephemeral=True
        )
    await inter.response.defer(ephemeral=True, with_message=True)
    files = _list_cog_files()
    if not files:
        return await inter.edit_original_response(
            embed=disnake.Embed(title="—・Cogs", description="Нет файлов в `cogs/`.", color=0x2b2d31)
        )
    lines = []
    for name in files:
        emoji = "🟢" if _is_loaded(name) else "🔴"
        lines.append(f"\u200b**・** {emoji} `{name}`")
    embed = disnake.Embed(
        title=f"🧩  Cogs — {BOT_NAME}",
        description="\n".join(lines),
        color=0x2b2d31
    )
    embed.set_footer(text=f"Всего: {len(files)} | Загружено: {sum(1 for n in files if _is_loaded(n))}")
    await inter.edit_original_response(embed=embed)


@bot.slash_command(name="load", description="Загрузить ког")
async def _load(inter, ког: str):
    if not await is_staff(inter.author.id):
        return await inter.response.send_message(
            embed=disnake.Embed(title="—・Ошибка", description="Доступ запрещён.", color=0xF6C4C5),
            ephemeral=True
        )
    await inter.response.defer(ephemeral=True, with_message=True)
    try:
        bot.load_extension(f"cogs.{ког}")
        print(f"[{BOT_NAME.upper()}] /load {ког} — OK", flush=True)
        await inter.edit_original_response(
            embed=disnake.Embed(title="—・Готово", description=f"Ког **{ког}** загружен.", color=0x9EE5B4)
        )
        test_guild = os.getenv("TEST_GUILD_ID", "").strip()
        if test_guild.isdigit():
            await sync_commands(int(test_guild))
    except commands.ExtensionAlreadyLoaded:
        await inter.edit_original_response(
            embed=disnake.Embed(title="—・Уже загружен", description=f"Ког **{ког}** уже работает. Используйте `/reload`.", color=0xF8E3A1)
        )
    except Exception as e:
        print(f"[{BOT_NAME.upper()}] /load {ког} — ERROR: {e}", flush=True)
        await inter.edit_original_response(
            embed=disnake.Embed(title="—・Ошибка", description=f"```{str(e)[:500]}```", color=0xF6C4C5)
        )


@bot.slash_command(name="unload", description="Выгрузить ког")
async def _unload(inter, ког: str):
    if not await is_staff(inter.author.id):
        return await inter.response.send_message(
            embed=disnake.Embed(title="—・Ошибка", description="Доступ запрещён.", color=0xF6C4C5),
            ephemeral=True
        )
    await inter.response.defer(ephemeral=True, with_message=True)
    try:
        bot.unload_extension(f"cogs.{ког}")
        print(f"[{BOT_NAME.upper()}] /unload {ког} — OK", flush=True)
        await inter.edit_original_response(
            embed=disnake.Embed(title="—・Готово", description=f"Ког **{ког}** выгружен.", color=0x9EE5B4)
        )
        test_guild = os.getenv("TEST_GUILD_ID", "").strip()
        if test_guild.isdigit():
            await sync_commands(int(test_guild))
    except commands.ExtensionNotLoaded:
        await inter.edit_original_response(
            embed=disnake.Embed(title="—・Не загружен", description=f"Ког **{ког}** не был загружен.", color=0xF8E3A1)
        )
    except Exception as e:
        print(f"[{BOT_NAME.upper()}] /unload {ког} — ERROR: {e}", flush=True)
        await inter.edit_original_response(
            embed=disnake.Embed(title="—・Ошибка", description=f"```{str(e)[:500]}```", color=0xF6C4C5)
        )


@bot.slash_command(name="reload", description="Перезагрузить ког")
async def _reload(inter, ког: str):
    if not await is_staff(inter.author.id):
        return await inter.response.send_message(
            embed=disnake.Embed(title="—・Ошибка", description="Доступ запрещён.", color=0xF6C4C5),
            ephemeral=True
        )
    await inter.response.defer(ephemeral=True, with_message=True)
    try:
        bot.reload_extension(f"cogs.{ког}")
        print(f"[{BOT_NAME.upper()}] /reload {ког} — OK", flush=True)
        await inter.edit_original_response(
            embed=disnake.Embed(title="—・Готово", description=f"Ког **{ког}** перезагружен.", color=0x9EE5B4)
        )
        test_guild = os.getenv("TEST_GUILD_ID", "").strip()
        if test_guild.isdigit():
            await sync_commands(int(test_guild))
    except commands.ExtensionNotLoaded:
        try:
            bot.load_extension(f"cogs.{ког}")
            await inter.edit_original_response(
                embed=disnake.Embed(title="—・Готово", description=f"Ког **{ког}** не был загружен — теперь загружен.", color=0x9EE5B4)
            )
            test_guild = os.getenv("TEST_GUILD_ID", "").strip()
            if test_guild.isdigit():
                await sync_commands(int(test_guild))
        except Exception as e:
            await inter.edit_original_response(
                embed=disnake.Embed(title="—・Ошибка", description=f"```{str(e)[:500]}```", color=0xF6C4C5)
            )
    except Exception as e:
        print(f"[{BOT_NAME.upper()}] /reload {ког} — ERROR: {e}", flush=True)
        await inter.edit_original_response(
            embed=disnake.Embed(title="—・Ошибка", description=f"```{str(e)[:500]}```", color=0xF6C4C5)
        )


def load_cogs():
    cogs_dir = Path(__file__).parent / "cogs"
    if not cogs_dir.exists():
        print(f"[{BOT_NAME.upper()}] Папка cogs/ не найдена: {cogs_dir}", flush=True)
        return
    for fn in sorted(cogs_dir.glob("*.py")):
        if fn.name.startswith("_"):
            continue
        try:
            bot.load_extension(f"cogs.{fn.stem}")
            print(f"[{BOT_NAME.upper()}] Загружен ког: {fn.stem}", flush=True)
        except Exception as e:
            print(f"[{BOT_NAME.upper()}] ❌ Не удалось загрузить ког {fn.stem}: {e}", flush=True)
            import traceback
            traceback.print_exc()


async def main():
    bot.pool = await init_pool()
    
    try:
        from shared.schema import ensure_core_schema
        async with bot.pool.acquire() as conn:
            await ensure_core_schema(conn)
            print(f"[{BOT_NAME.upper()}] Таблицы БД созданы/проверены", flush=True)
    except Exception as e:
        print(f"[{BOT_NAME.upper()}] ⚠️ Ошибка создания таблиц: {e}", flush=True)
    
    try:
        bot.redis = await init_redis()
    except Exception as e:
        print(f"[{BOT_NAME.upper()}] ⚠️ Redis недоступен: {e}", flush=True)
        bot.redis = None

    if start_config_listener is not None:
        try:
            await start_config_listener()
        except Exception as e:
            print(f"[{BOT_NAME.upper()}] ⚠️ start_config_listener: {e}", flush=True)
    elif _legacy_start_listener is not None:
        try:
            await _legacy_start_listener()
        except Exception as e:
            print(f"[{BOT_NAME.upper()}] ⚠️ legacy start_listener: {e}", flush=True)

    sys.path.insert(0, str(Path(__file__).parent))
    load_cogs()

    token_var = f"BOT_TOKEN_{BOT_NAME.upper()}"
    token = os.getenv(token_var)
    if not token:
        print(f"[{BOT_NAME.upper()}] ❌ {token_var} не задан в .env", flush=True)
        sys.exit(1)

    retries = 0
    max_retries = 100
    while retries < max_retries:
        try:
            await bot.start(token, reconnect=True)
            print(f"[{BOT_NAME.upper()}] ⚠️ bot.start вернулся (WS lost). Retry #{retries + 1}", flush=True)
        except disnake.LoginFailure as e:
            print(f"[{BOT_NAME.upper()}] ❌ Invalid token: {e}", flush=True)
            break
        except (disnake.ConnectionClosed, disnake.GatewayNotFound, asyncio.TimeoutError) as e:
            print(f"[{BOT_NAME.upper()}] ⚠️ Connection error: {type(e).__name__}: {e}. Retry #{retries + 1}", flush=True)
        except Exception as e:
            import traceback
            print(f"[{BOT_NAME.upper()}] ❌ Unexpected exception in bot.start: {type(e).__name__}: {e}", flush=True)
            traceback.print_exc()
        retries += 1
        delay = min(5 * (2 ** min(retries - 1, 6)), 300)
        print(f"[{BOT_NAME.upper()}] Reconnect через {delay} сек...", flush=True)
        await asyncio.sleep(delay)

        try:
            if not bot.is_closed():
                await bot.close()
        except Exception:
            pass

    if _legacy_stop_listener is not None:
        try:
            await _legacy_stop_listener()
        except Exception:
            pass
    await close_redis()
    await close_pool()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(f"[{BOT_NAME.upper()}] Остановлен.", flush=True)