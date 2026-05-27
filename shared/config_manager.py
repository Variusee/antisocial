import asyncio
import json
import time
from typing import Optional
import asyncpg
from pydantic import ValidationError

from .config_models import RootConfig
from .database import init_pool

_current_config: Optional[RootConfig] = None
_subscribers: list = []
_listener_task: Optional[asyncio.Task] = None

CONFIG_KEY = "global"
NOTIFY_CHANNEL = "antisocial_config_changed"


async def ensure_config_tables(conn: asyncpg.Connection):
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS bot_config (
            key TEXT PRIMARY KEY,
            data JSONB NOT NULL,
            updated_at BIGINT NOT NULL,
            updated_by BIGINT DEFAULT 0
        )
    """)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS bot_config_history (
            id SERIAL PRIMARY KEY,
            key TEXT NOT NULL,
            data JSONB NOT NULL,
            updated_at BIGINT NOT NULL,
            updated_by BIGINT DEFAULT 0
        )
    """)
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_bot_config_history_key ON bot_config_history(key, updated_at DESC)")


async def load_config(force: bool = False) -> RootConfig:
    global _current_config
    if _current_config is not None and not force:
        return _current_config
    pool = await init_pool()
    async with pool.acquire() as conn:
        await ensure_config_tables(conn)
        row = await conn.fetchrow("SELECT data FROM bot_config WHERE key = $1", CONFIG_KEY)
    if row:
        raw = row['data']
        if isinstance(raw, str):
            raw = json.loads(raw)
        try:
            _current_config = RootConfig(**raw)
        except ValidationError as e:
            print(f"[CONFIG][ERROR] Валидация из БД упала: {e}")
            _current_config = RootConfig.model_construct(**raw)
    else:
        _current_config = RootConfig()
        pool2 = await init_pool()
        async with pool2.acquire() as conn2:
            await conn2.execute(
                "INSERT INTO bot_config (key, data, updated_at, updated_by) VALUES ($1, $2::jsonb, $3, 0) ON CONFLICT DO NOTHING",
                CONFIG_KEY, json.dumps(_current_config.model_dump()), int(time.time())
            )
    return _current_config


async def save_config(new_cfg: RootConfig, updated_by: int = 0) -> RootConfig:
    global _current_config
    pool = await init_pool()
    data = new_cfg.model_dump()
    ts = int(time.time())
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO bot_config (key, data, updated_at, updated_by)
                VALUES ($1, $2::jsonb, $3, $4)
                ON CONFLICT (key) DO UPDATE SET data = $2::jsonb, updated_at = $3, updated_by = $4
                """,
                CONFIG_KEY, json.dumps(data), ts, updated_by
            )
            await conn.execute(
                "INSERT INTO bot_config_history (key, data, updated_at, updated_by) VALUES ($1, $2::jsonb, $3, $4)",
                CONFIG_KEY, json.dumps(data), ts, updated_by
            )
            await conn.execute(f"NOTIFY {NOTIFY_CHANNEL}, '{CONFIG_KEY}'")
    _current_config = new_cfg
    for cb in _subscribers:
        try:
            if asyncio.iscoroutinefunction(cb):
                asyncio.create_task(cb(new_cfg))
            else:
                cb(new_cfg)
        except Exception:
            pass
    return new_cfg


def subscribe(callback):
    _subscribers.append(callback)


async def _listener_loop():
    pool = await init_pool()
    while True:
        conn = None
        try:
            conn = await pool.acquire()

            async def handler(connection, pid, channel, payload):
                if payload == CONFIG_KEY:
                    new_cfg = await load_config(force=True)
                    for cb in _subscribers:
                        try:
                            if asyncio.iscoroutinefunction(cb):
                                asyncio.create_task(cb(new_cfg))
                            else:
                                cb(new_cfg)
                        except Exception:
                            pass

            await conn.add_listener(NOTIFY_CHANNEL, handler)
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[CONFIG LISTENER] {e}, reconnecting in 5s...")
            if conn:
                try:
                    await pool.release(conn)
                except Exception:
                    pass
            await asyncio.sleep(5)


async def start_listener():
    global _listener_task
    if _listener_task is None:
        _listener_task = asyncio.create_task(_listener_loop())


async def stop_listener():
    global _listener_task
    if _listener_task:
        _listener_task.cancel()
        try:
            await _listener_task
        except asyncio.CancelledError:
            pass
        _listener_task = None
